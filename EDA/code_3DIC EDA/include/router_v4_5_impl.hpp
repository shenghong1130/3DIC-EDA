#pragma once
#include "sparse_tsv_assignment.hpp"
#ifndef GR_V4_4
#error V4.5 requires the unchanged V4.4 pipeline
#endif
namespace gr {
int Router::vias_v45(const Route&r)const {
    int n=0;for(auto e:r.edges){int x,y,l,a,b,k;decode(e.a,x,y,l);decode(e.b,a,b,k);n+=l!=k&&std::min(l,k)!=10;}return n;
}

void Router::plan_v45(){
    Progress::Scope log(progress,"V45_GLOBAL_TSV_PLAN");double began=elapsed();
    v45_stats_["initial"]=Json::object();auto&stat=v45_stats_["initial"];
    stat["scope"]="sparse footprint/channel proxy proposal; complete Route acceptance required";
    size_t entries=0;struct Budget{const char*reason;};const size_t resource_limit=resources.size()+250000;
    auto poll=[&](){check_time();if(elapsed()-began>30)throw Budget{"time"};if(resources.size()>resource_limit)throw Budget{"resource_cache"};};
    try{
        tsv_assignment::Problem p;std::vector<size_t>indices;
        std::map<size_t,std::vector<Id>>sites;std::map<size_t,Id>anchors;
        std::vector<int>counts(v3_thermal_rise_.size());
        for(size_t i=0;i<d.nets.size();i++){
            poll();auto&n=d.nets[i];if(!n.cross)continue;
            Id base=select_tsv(n);anchors[i]=base;if(v3_thermal_ready_)++counts[v3_thermal_cell(base)];
            int x,y,l;decode(base,x,y,l);std::set<Id>unique{base};
            for(int r:{1,2,4,8})for(int dx:{-r,0,r})for(int dy:{-r,0,r})
                if(x+dx>=0&&x+dx<nx&&y+dy>=0&&y+dy<ny)unique.insert(id(x+dx,y+dy,10));
            std::vector<std::pair<double,Id>>rank;
            for(Id site:unique){poll();if(!tsv_legal(site))continue;int sx,sy,sl;decode(site,sx,sy,sl);auto[cx,cy]=center(site);
                double access=0,pressure=0;size_t samples=0;
                // Sample both-die access corridors on the actual Pin layers.
                // This is a bounded proposal heuristic, not claimed routability.
                size_t stride=std::max(size_t(1),(n.pins.size()+15)/16);
                for(size_t k=0;k<n.pins.size();k+=stride){auto&pin=n.pins[k];access+=(std::abs(pin.x-cx)+std::abs(pin.y-cy))/unit;
                    int px,py,pl;decode(pin_node(pin),px,py,pl);
                    for(int step=0;step<=4;step++){
                        int xx=px+(sx-px)*step/4,yy=py+(sy-py)*step/4;
                        double best=1e6;
                        for(int dl=0;dl<3;dl++){int layer=std::clamp(pl+dl,pl<=10?1:11,pl<=10?10:20);Id v=id(xx,yy,layer);
                            // The physical fixed-demand model is already built.
                            // Missing resource entries have F=U=0. Read-only
                            // sampling avoids populating empty search resources.
                            int cap=scoring_capacity(v);auto found=resources.find(v);
                            int demand=found==resources.end()?0:found->second.fixed+found->second.usage;
                            if(cap>0)best=std::min(best,double(demand+1)/cap);}
                        pressure+=std::min(best,100.);++samples;
                    }
                }
                rank.emplace_back(access/std::max(size_t(1),(n.pins.size()+stride-1)/stride)+8*pressure/std::max(size_t(1),samples),site);
            }
            std::sort(rank.begin(),rank.end());if(rank.size()>16){auto b=std::find_if(rank.begin(),rank.end(),[&](auto a){return a.second==base;});auto original=*b;rank.resize(16);if(std::none_of(rank.begin(),rank.end(),[&](auto a){return a.second==base;}))rank.back()=original;}
            std::vector<tsv_assignment::Choice> choices;
            for(auto[cost,site]:rank){Route fp;fp.cross=true;fp.tsv=site;prepare_v34_route(n,fp);tsv_assignment::Choice c;c.site=site;c.access_cost=cost;
                bool legal=true;for(auto[v,demand]:v34_usage(fp)){int cap=scoring_capacity(v);if(cap<=0){legal=false;break;}auto found=resources.find(v);int fixed=found==resources.end()?0:found->second.fixed;c.demand.emplace_back(v,demand);p.resources.emplace(v,tsv_assignment::Resource{cap,fixed});}
                if(legal){entries+=c.demand.size();if(entries>2000000)throw Budget{"footprint_entries"};choices.push_back(std::move(c));sites[i].push_back(site);}
            }
            if(!choices.empty()){indices.push_back(i);p.nets.push_back(std::move(choices));}
        }
        auto solution=tsv_assignment::solve(p,poll);
        for(size_t j=0;j<indices.size();j++){Id chosen=p.nets[j][solution.choice[j]].site;auto&list=sites[indices[j]];auto it=std::find(list.begin(),list.end(),chosen);if(it!=list.end())std::rotate(list.begin(),it,it+1);if(list.size()>size_t(v45.topk))list.resize(v45.topk);}
        if(v3_thermal_ready_)v45_density_=std::make_unique<v45::Thermal>(v3_thermal_rise_,std::move(counts));
        v45_sites_.swap(sites);v45_anchor_.swap(anchors);
        stat["status"]="PROPOSALS_READY";stat["planned_nets"]=double(indices.size());stat["coordinate_moves"]=double(solution.moves);stat["pair_swaps"]=double(solution.swaps);stat["footprint_proxy_phi"]=solution.phi;
    }catch(const Budget&b){stat["status"]="PLANNING_BUDGET_FALLBACK";stat["budget_reason"]=b.reason;}
    catch(const std::bad_alloc&){v45_sites_.clear();v45_anchor_.clear();v45_density_.reset();stat["status"]="ALLOCATION_FALLBACK";}
    stat["entries"]=double(entries);stat["seconds"]=elapsed()-began;stat["attempts"]=0;stat["accepted"]=0;stat["skipped"]=0;stat["rejections"]=Json::object();
    v45_preview_end_=elapsed()+30;
}

// Complete pin-access/Steiner/3D maze preview. All state, including search
// window and the active Pin exemption set, is restored on every exit path.
Route Router::preview_v45(size_t i,const Route&old,Id site,Id seed){
    bool a=v4_search_active_,b=rerouting_,c=v44_active_;double deadline=reroute_deadline_seconds_;Id forced=v3_forced_tsv_;size_t limit=v4_search_limit_;
    std::array<int,4>bounds{v4_xl_,v4_xh_,v4_yl_,v4_yh_};auto pins=std::move(v34_active_pin_cells_);
    struct Reset{Router&r;bool a,b,c;double deadline;Id forced;size_t limit;std::array<int,4>bounds;std::vector<Id>&pins;
        ~Reset(){r.v4_search_active_=a;r.rerouting_=b;r.v44_active_=c;r.reroute_deadline_seconds_=deadline;r.v3_forced_tsv_=forced;r.v4_search_limit_=limit;r.v4_xl_=bounds[0];r.v4_xh_=bounds[1];r.v4_yl_=bounds[2];r.v4_yh_=bounds[3];r.v34_active_pin_cells_=std::move(pins);}}
        reset{*this,a,b,c,deadline,forced,limit,bounds,pins};
    v34_active_pin_cells_=own_pin_cells(d.nets[i]);v4_search_active_=rerouting_=v44_active_=true;v4_search_limit_=24000;
    reroute_deadline_seconds_=deadline>0?std::min(deadline,elapsed()+.25):elapsed()+.25;check_time();
    Route out;
#ifdef GR_V45_TEST
    if(v45_candidate_hook_)out=v45_candidate_hook_(i,old,site,seed);
    else
#endif
    if(seed)out=v44_patch(i,old,seed);
    else {
        if(old.nodes.size()>10000)throw V3Budget();
        if(site&&!tsv_legal(site))throw V3Budget();
        if(d.nets[i].pins.size()>64||old.nodes.size()>4096){
            // High-Pin Nets retain their tree: move only bounded TSV tails.
            if(!site||site==old.tsv)throw V3Budget();out=v4_local_tsv(i,old,site);
        }else{
            int xl=nx,xh=0,yl=ny,yh=0;
            auto add=[&](Id v){int x,y,l;decode(v,x,y,l);xl=std::min(xl,x);xh=std::max(xh,x);yl=std::min(yl,y);yh=std::max(yh,y);};
            for(Id v:old.nodes)add(v);if(site)add(site);
            for(auto&p:d.nets[i].pins)for(auto rect:pin_geometry(p))geometry_cells(rect,[&](Id v,const Rect&){add(v);});
            v4_xl_=std::max(0,xl-8);v4_xh_=std::min(nx-1,xh+8);v4_yl_=std::max(0,yl-8);v4_yh_=std::min(ny-1,yh+8);
            if(v4_xh_-v4_xl_>128||v4_yh_-v4_yl_>128)throw V3Budget();
            v3_forced_tsv_=site;out=route(d.nets[i]);
        }
    }
    out.guides.clear();make_guides(out);prepare_v34_route(d.nets[i],out);validate(d.nets[i],out);
    if(out.cross&&out.tsv!=(site?site:old.tsv))throw std::runtime_error("V45 TSV identity mismatch");
    return out;
}

Route Router::initial_v45(size_t i){
    Route base=route(d.nets[i]);auto it=v45_sites_.find(i);if(it==v45_sites_.end())return base;
    auto&stat=v45_stats_["initial"];auto reject=[&](const char*why){auto&v=stat["rejections"][why];v=(v.type==Json::Number?v.number():0)+1;};
    Id initial=base.tsv;v45::Thermal::Update actual;
    if(v45_density_){actual=v45_density_->preview({{v3_thermal_cell(v45_anchor_.at(i)),v3_thermal_cell(initial)}});v45_density_->apply(actual);}
    const auto before=v36_insertion(base);double wire=v43_reference_wire(base);
    // The complete baseline route is kept alive until a validated candidate
    // strictly improves its real insertion cost. Preview never occupies.
    v45::Quality q{before.phi,wire,v45_density_?v45_density_->value():0,before.overflow,vias_v45(base)},best=q;
    v45::Limits limits;limits.wire_growth=0;limits.rho_loss=v45.thermal?1e-5:2;limits.max_vias=4;
    v45::Thermal::Update chosen;
    if(v45_density_)chosen=v45_density_->preview({});
    if(before.phi<.01||!std::isfinite(wire)||elapsed()>=v45_preview_end_||v45_previews_>=512){stat["skipped"]=stat["skipped"].number()+1;return base;}
    for(Id site:it->second){if(site==initial||elapsed()>=v45_preview_end_||v45_previews_>=512)continue;++v45_previews_;stat["attempts"]=stat["attempts"].number()+1;
        try{
            v45::Thermal::Update change;if(v45_density_){change=v45_density_->preview({{v3_thermal_cell(initial),v3_thermal_cell(site)}});if(v45.thermal&&v45_density_->after(change)<q.rho-1e-5){reject("thermal");continue;}}
            auto candidate=preview_v45(i,base,site,0);auto delta=v36_insertion(candidate);
            v45::Quality next{delta.phi,v43_reference_wire(candidate),v45_density_?v45_density_->after(change):0,delta.overflow,vias_v45(candidate)};
            const char*why=v45::reject(q,best,next,limits);if(why){reject(why);continue;}
            base=std::move(candidate);best=next;chosen=std::move(change);
        }catch(const RerouteBudget&){reject("preview_time");}catch(const V3Budget&){reject("preview_search_or_workspace");}
        catch(const std::bad_alloc&){reject("preview_allocation");break;}
        catch(const RouterTimeout&){throw;}
        catch(const std::runtime_error&e){std::string s=e.what();if(s.find("No legal")==0||s.find("A* expansion")==0||s.find("Disconnected")==0||s.find("Nonunique TSV")==0)reject("infeasible_path");else throw;}
    }
    if(base.tsv!=initial){if(v45_density_)v45_density_->apply(chosen);stat["accepted"]=stat["accepted"].number()+1;}
    return base;
}

void Router::optimize_v45(){
    Progress::Scope log(progress,"V45_QUALITY_NEGOTIATION");double began=elapsed();
    v45_stats_["status"]="EXPERIMENTAL";v45_stats_["official_metrics"]=false;
    v45_stats_["mode"]=v45.mode;v45_stats_["path"]=v45.path;v45_stats_["thermal"]=v45.thermal;v45_stats_["repair"]=v45.repair;
    auto snapshot=[&](){auto a=batch_quality_full();v45::Quality q;q.phi=a.phi;q.overflow=a.overflow;
        for(auto&r:routes){double w=v43_reference_wire(r);if(!std::isfinite(w))throw V3Budget();q.wire+=w;q.vias+=vias_v45(r);}
        if(v3_thermal_ready_){std::vector<int>counts(v3_thermal_rise_.size());for(auto&r:routes)if(r.cross)++counts[v3_thermal_cell(r.tsv)];v45_density_=std::make_unique<v45::Thermal>(v3_thermal_rise_,std::move(counts));q.rho=v45_density_->value();}return q;};
    auto json=[](const v45::Quality&q){Json j=Json::object();j["phi"]=q.phi;j["overflow"]=double(q.overflow);j["wire_dbu_proxy"]=q.wire;j["thermal_correlation"]=q.rho;j["vias"]=double(q.vias);return j;};
    v45::Quality base;try{base=snapshot();}catch(const V3Budget&){v45_stats_["status"]="WIRE_WORKSPACE_FALLBACK";return;}
    auto current=base;v45_stats_["before"]=json(base);Json rows=Json::array();
    v45::Limits limits;limits.mode=v45::Mode(v45.mode);limits.wire_growth=v45.wire_growth;limits.rho_loss=v45.thermal?1e-5:2;limits.max_vias=64;
    if(!v45.repair&&!v45.thermal&&v45.mode==0){v45_stats_["after"]=json(current);v45_stats_["rounds"]=rows;return;}
    const size_t resource_limit=resources.size()+100000;std::vector<unsigned char>failures(routes.size(),0);
    bool oldactive=v44_active_;v44_active_=true;
    struct Active{bool&v;bool old;~Active(){v=old;}}active{v44_active_,oldactive};
    for(int round=0;round<v45.rounds&&elapsed()-began<v45.seconds;round++){
        auto prior=current;double t0=elapsed();
        // An outer transaction per changed Net remains alive until the full
        // round audit. Each Net is visited once; reverse rollback is heap-free.
        std::vector<std::unique_ptr<V36Transaction>>journal;journal.reserve(1024);
        struct Undo {std::vector<std::unique_ptr<V36Transaction>>&j;~Undo(){for(auto i=j.rbegin();i!=j.rend();++i)(*i)->rollback();}}undo{journal};
        std::set<size_t>touched;std::map<Id,std::vector<size_t>>owners;
        std::vector<std::pair<double,size_t>>rank;size_t owner_entries=0;
        for(size_t i=0;i<routes.size();i++){if(i%128==0)check_time();if(failures[i]>=2||routes[i].nodes.size()>10000)continue;double gain=0;
            for(auto[v,n]:v34_usage(routes[i])){auto&s=resource(v);auto a=scoring_cell(v,s.usage),b=scoring_cell(v,s.usage-n);double g=a.phi-b.phi;
                gain+=g;if(g>0&&owner_entries<100000){auto&list=owners[v];if(list.size()<4){list.push_back(i);++owner_entries;}}}
            if(gain>1e-9)rank.emplace_back(-gain,i);
        }std::sort(rank.begin(),rank.end());if(rank.size()>1024)rank.resize(1024);
        std::map<std::string,int>reasons;size_t attempted=0,accepted=0,pairs=0,full=0,patches=0;
        auto trial=[&](const std::vector<size_t>&ids,const std::vector<Id>&sites,const std::vector<Id>&seeds)->bool{
            if(attempted>=512||elapsed()-began>=v45.seconds||resources.size()>resource_limit)return false;
            for(size_t i:ids)if(touched.count(i))return false;
            // Bound retained undo geometry, not just the number of Nets.
            size_t undo_nodes=0;for(const auto&tx:journal)undo_nodes+=tx->old.nodes.size();
            for(size_t i:ids)undo_nodes+=routes[i].nodes.size();
            if(undo_nodes>100000){++reasons["round_undo_workspace"];return false;}
            ++attempted;size_t first=journal.size();
            auto rollback=[&](){while(journal.size()>first){journal.back()->rollback();journal.pop_back();}};
            // Build ledger entries before mutation. A failure cannot escape
            // without the outer Undo restoring all previously accepted Nets.
            try{
                auto next=current;v45::Thermal::Moves moves;
                for(size_t k=0;k<ids.size();k++){size_t i=ids[k];if(sites[k]&&routes[i].cross&&v45_density_)moves.emplace_back(v3_thermal_cell(routes[i].tsv),v3_thermal_cell(sites[k]));}
                v45::Thermal::Update thermal;if(v45_density_){thermal=v45_density_->preview(moves);next.rho=v45_density_->after(thermal);if(next.rho<base.rho-limits.rho_loss){++reasons["thermal_guard"];return false;}}
                for(size_t i:ids)journal.push_back(std::make_unique<V36Transaction>(*this,i));
                // Simultaneous removal, then sequential insertion gives the
                // exact shared-footprint joint delta including cross terms.
                double oldphi=0;long long oldover=0;
                for(size_t k=first;k<journal.size();k++){auto&tx=*journal[k];auto a=v36_insertion(tx.old);oldphi+=a.phi;oldover+=a.overflow;occupy(tx.old,1);}
                for(size_t k=journal.size();k>first;k--)occupy(journal[k-1]->old,-1);
                next.phi-=oldphi;next.overflow-=oldover;
                for(size_t k=0;k<ids.size();k++){auto&tx=*journal[first+k];auto candidate=preview_v45(ids[k],tx.old,sites[k],seeds[k]);
                    auto a=v36_insertion(candidate);next.phi+=a.phi;next.overflow+=a.overflow;
                    next.wire+=v43_reference_wire(candidate)-v43_reference_wire(tx.old);next.vias+=vias_v45(candidate)-vias_v45(tx.old);
                    tx.install(std::move(candidate));
#ifdef GR_V45_TEST
                    if(v45_install_hook_)v45_install_hook_();
#endif
                }
                if(const char*why=v45::reject(base,current,next,limits)){++reasons[why];rollback();return false;}
                for(size_t i:ids)touched.insert(i); // allocating work BEFORE thermal/quality commit
                if(v45_density_)v45_density_->apply(thermal);current=next;accepted+=ids.size();pairs+=ids.size()>1;full+=seeds[0]==0;patches+=seeds[0]!=0;return true;
            }catch(const RerouteBudget&){++reasons["candidate_time_budget"];rollback();return false;}
            catch(const V3Budget&){++reasons["search_or_workspace"];rollback();return false;}
            catch(const std::bad_alloc&){rollback();throw;}
            catch(const RouterTimeout&){rollback();throw;}
            catch(const std::runtime_error&e){rollback();std::string s=e.what();if(s.find("No legal")==0||s.find("A* expansion")==0||s.find("Disconnected")==0||s.find("Nonunique TSV")==0){++reasons["infeasible"];return false;}throw;}
        };
        for(auto[priority,i]:rank){(void)priority;check_time();if(attempted>=512||elapsed()-began>=v45.seconds||resources.size()>resource_limit)break;if(touched.count(i))continue;
            bool improved=false;Id seed=0,hot=0;double gain=0,hotgain=0;
            std::map<Id,int>degree;for(auto e:routes[i].edges){++degree[e.a];++degree[e.b];}
            std::set<Id>keep(routes[i].pin_access_nodes.begin(),routes[i].pin_access_nodes.end());if(routes[i].cross){keep.insert(routes[i].tsv);keep.insert(routes[i].tsv+Id(nx)*ny);}
            for(auto[v,n]:v34_usage(routes[i])){auto&s=resource(v);double g=scoring_cell(v,s.usage).phi-scoring_cell(v,s.usage-n).phi;
                if(g>hotgain){hotgain=g;hot=v;}if(g>gain&&degree[v]==2&&!keep.count(v)){gain=g;seed=v;}}
            if(v45.repair||v45.mode!=0){if(seed)improved=trial({i},{0},{seed});else ++reasons["no_movable_branch"];}
            if(!improved&&v45.thermal&&routes[i].cross){
                int x,y,l;decode(routes[i].tsv,x,y,l);std::vector<std::pair<double,Id>>sites;
                for(int rad:{1,2,4,8})for(auto[dx,dy]:std::vector<std::pair<int,int>>{{rad,0},{-rad,0},{0,rad},{0,-rad}}){
                    if(x+dx<0||x+dx>=nx||y+dy<0||y+dy>=ny)continue;Id site=id(x+dx,y+dy,10);if(!tsv_legal(site))continue;
                    double rho=current.rho;if(v45_density_)rho=v45_density_->after(v45_density_->preview({{v3_thermal_cell(routes[i].tsv),v3_thermal_cell(site)}}));
                    if(rho<base.rho-limits.rho_loss)continue;
                    Route fp;fp.cross=true;fp.tsv=site;prepare_v34_route(d.nets[i],fp);auto q=v36_insertion(fp);sites.emplace_back(q.phi-10*(rho-current.rho),site);
                }std::sort(sites.begin(),sites.end());if(sites.size()>2)sites.resize(2);
                for(auto[cost,site]:sites){(void)cost;if(trial({i},{site},{0})){improved=true;break;}}
            }
            if(!improved&&v45.repair&&hot){
                // Sparse owner index restricts joint work to real co-users.
                auto found=owners.find(hot);if(found!=owners.end())for(size_t other:found->second){
                    if(other==i||touched.count(other)||d.nets[other].pins.size()>64||d.nets[i].pins.size()>64)continue;
                    std::vector<Id>sites{routes[i].cross?routes[i].tsv:0,routes[other].cross?routes[other].tsv:0};
                    if(routes[i].cross&&routes[other].cross&&routes[i].tsv!=routes[other].tsv)std::swap(sites[0],sites[1]);
                    improved=trial({i,other},sites,{0,0});break;
                }
            }
            if(improved)failures[i]=0;else ++failures[i];
            if(progress)progress->poll();
        }
        auto measured=snapshot();bool audit=std::abs(measured.phi-current.phi)<=1e-6*std::max(1.,measured.phi)&&measured.overflow==current.overflow&&std::abs(measured.wire-current.wire)<.01&&std::abs(measured.rho-current.rho)<1e-9;
        if(accepted&&audit&&!v45::reject(base,prior,measured,limits)){
            for(auto&tx:journal){validate(d.nets[tx->index],routes[tx->index]);}
            // Commit only after ALL allocating validation and audit work.
            for(auto&tx:journal)tx->committed=true;current=measured;
        }else {for(auto it=journal.rbegin();it!=journal.rend();++it)(*it)->rollback();current=snapshot();++reasons[audit?"round_no_gain":"round_audit_mismatch"];}
        Json row=Json::object();row["round"]=round;row["candidates"]=double(rank.size());row["attempted"]=double(attempted);row["accepted_nets"]=double(accepted);row["pair_groups"]=double(pairs);row["full_tree"]=double(full);row["patches"]=double(patches);row["full_audit_passed"]=audit;row["before"]=json(prior);row["after"]=json(current);row["seconds"]=elapsed()-t0;row["peak_rss_bytes"]=Json();row["peak_rss_source"]="resource_monitor.csv / router_exit.json";
        Json rej=Json::object();for(auto[k,n]:reasons)rej[k]=n;row["rejections"]=rej;rows.push(row);
        if(!accepted||!audit)break;
        for(auto[v,list]:owners){(void)list;auto&s=resource(v);if(scoring_cell(v,s.usage).overflow>scoring_cell(v,0).overflow){auto it=v44_history_.find(v);if(it!=v44_history_.end())it->second=std::min(16.,it->second+1);else if(v44_history_.size()<200000)v44_history_.emplace(v,1.);}}
    }
    v45_stats_["after"]=json(current);v45_stats_["rounds"]=rows;v45_stats_["seconds"]=elapsed()-began;v45_stats_["wire_growth"]=v45.wire_growth;
}
}
