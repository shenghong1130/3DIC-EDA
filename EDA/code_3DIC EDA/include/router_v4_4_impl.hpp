#pragma once
#ifndef GR_V4_3
#error V4.4 extends the preserved V4.3 routing pipeline
#endif
namespace gr {
// Positive-capacity prices only. Nonnegative additions preserve the geometric
// A* lower bound. History is private to this phase, bounded and sparse.
double Router::v44_price(Id v,int demand){
    if(demand<0)throw std::runtime_error("Negative V44 demand");
    if(!demand)return 0;
    auto&s=resource(v);auto a=scoring_cell(v,s.usage);
    if(s.scoring_capacity<=0)return std::numeric_limits<double>::infinity();
    if(std::binary_search(v34_active_pin_cells_.begin(),v34_active_pin_cells_.end(),v))return 0;
    auto b=scoring_cell(v,s.usage+demand);double cap=s.scoring_capacity;
    auto h=v44_history_.find(v);double history=h==v44_history_.end()?0:h->second;
    double price=v44.cost?v44_present_*(2*double(b.overflow-a.overflow)+32*std::max(0.0,b.phi-a.phi)+.1*std::pow(std::min(1.0,(s.scoring_fixed+s.usage+demand)/cap),4)):
        scoring_aware_congestion_cost(v,demand);
    return price+history;
}
// Longer degree-two chains can escape in Z; pin access and branch terminals
// are immutable. This is a bounded 3D maze patch, not a full topology rewrite.
Route Router::v44_patch(size_t i,const Route&old,Id seed){
    std::map<Id,std::vector<Id>>adj;for(auto e:old.edges){adj[e.a].push_back(e.b);adj[e.b].push_back(e.a);}
    std::set<Id>keep(old.pin_access_nodes.begin(),old.pin_access_nodes.end());
    if(keep.empty())for(auto&p:d.nets[i].pins)keep.insert(pin_node(p));
    if(old.cross){keep.insert(old.tsv);keep.insert(old.tsv+Id(nx)*ny);}
    if(keep.count(seed)||adj[seed].size()!=2)throw V3Budget();
    std::set<Id>removed{seed};std::vector<Edge>erased;
    auto walk=[&](Id at){Id prev=seed;erased.push_back({std::min(prev,at),std::max(prev,at)});
        for(int k=0;k<(v44.layer?32:8);k++){
            if(keep.count(at)||adj[at].size()!=2||removed.count(at))break;
            removed.insert(at);Id next=adj[at][0]==prev?adj[at][1]:adj[at][0];prev=at;at=next;erased.push_back({std::min(prev,at),std::max(prev,at)});
        }return at;};
    Id a=walk(adj[seed][0]),b=walk(adj[seed][1]);if(a==b||removed.count(a)||removed.count(b))throw V3Budget();
    Route out=old;for(auto v:removed)out.nodes.erase(v);for(auto e:erased)out.edges.erase(e);
    int ax,ay,al,bx,by,bl;decode(a,ax,ay,al);decode(b,bx,by,bl);if((al<=10)!=(bl<=10))throw V3Budget();
    int margin=v44.layer?12:4;
    v4_xl_=std::max(0,std::min(ax,bx)-margin);v4_xh_=std::min(nx-1,std::max(ax,bx)+margin);
    v4_yl_=std::max(0,std::min(ay,by)-margin);v4_yh_=std::min(ny-1,std::max(ay,by)+margin);
    if(v4_xh_-v4_xl_>128||v4_yh_-v4_yl_>128)throw V3Budget();
    int depth=v44.layer?4:2,lo=std::max(al<=10?1:11,std::min(al,bl)-depth),hi=std::min(al<=10?10:20,std::max(al,bl)+depth);
    auto p=search(a,b,lo,hi,old.cross);for(Id v:p)out.nodes.insert(v);for(size_t k=1;k<p.size();k++)out.edges.insert({std::min(p[k-1],p[k]),std::max(p[k-1],p[k])});
    out.steiner_points.erase(std::remove_if(out.steiner_points.begin(),out.steiner_points.end(),[&](Id v){return !out.nodes.count(v);}),out.steiner_points.end());return out;
}
bool Router::v44_trial(size_t i,Id seed,Id site,double&wire,double limit,Json&detail){
    detail=Json::object();detail["accepted"]=false;
    if(i>=routes.size()||routes[i].nodes.size()>10000){detail["reason"]="workspace";return false;}
    const double old_wire=v43_reference_wire(routes[i]);
    if(!std::isfinite(old_wire)){detail["reason"]="unknown_wire";return false;}
    bool active=v4_search_active_,phase=rerouting_;double deadline=reroute_deadline_seconds_;Id forced=v3_forced_tsv_;size_t expansions=v4_search_limit_;
    auto pins=std::move(v34_active_pin_cells_);
    struct Reset{Router&r;bool a,b;double deadline;Id tsv;size_t expansions;std::vector<Id>&pins;~Reset(){r.v4_search_active_=a;r.rerouting_=b;r.reroute_deadline_seconds_=deadline;r.v3_forced_tsv_=tsv;r.v4_search_limit_=expansions;r.v34_active_pin_cells_=std::move(pins);}}
        reset{*this,active,phase,deadline,forced,expansions,pins};
    v34_active_pin_cells_=own_pin_cells(d.nets[i]);rerouting_=true;v4_search_active_=true;v4_search_limit_=24000;
    reroute_deadline_seconds_=deadline>0?std::min(deadline,elapsed()+.4):elapsed()+.4;
    V36Transaction tx(*this,i);auto before=v36_insertion(tx.old);
    try{
        check_time();Route candidate;
        if(site){
            if(!tsv_legal(site)||!tx.old.cross){detail["reason"]="illegal_site";return false;}
            // Reconnect both complete die trees for small Nets; unlike short
            // TSV tails this can change branches limiting the old refinement.
            if(d.nets[i].pins.size()>32||tx.old.nodes.size()>4096){detail["reason"]="tsv_full_tree_workspace";return false;}
            int sx,sy,sl;decode(site,sx,sy,sl);int xl=sx,xh=sx,yl=sy,yh=sy;
            // The old geometry plus target bounds every full-tree search;
            // keep the 24K expansion guard active even for a complete Net.
            for(Id v:tx.old.nodes){int x,y,l;decode(v,x,y,l);xl=std::min(xl,x);xh=std::max(xh,x);yl=std::min(yl,y);yh=std::max(yh,y);}
            for(const auto&p:d.nets[i].pins)for(const auto&rect:pin_geometry(p))geometry_cells(rect,[&](Id v,const Rect&){int x,y,l;decode(v,x,y,l);xl=std::min(xl,x);xh=std::max(xh,x);yl=std::min(yl,y);yh=std::max(yh,y);});
            v4_xl_=std::max(0,xl-12);v4_xh_=std::min(nx-1,xh+12);v4_yl_=std::max(0,yl-12);v4_yh_=std::min(ny-1,yh+12);
            if(v4_xh_-v4_xl_>128||v4_yh_-v4_yl_>128){detail["reason"]="tsv_search_window";return false;}
            v3_forced_tsv_=site;candidate=route(d.nets[i]);
        }else candidate=v44_patch(i,tx.old,seed);
        candidate.guides.clear();make_guides(candidate);prepare_v34_route(d.nets[i],candidate);validate(d.nets[i],candidate);
        if(candidate.tsv!=(site?site:tx.old.tsv))throw std::runtime_error("V44 changed TSV identity unexpectedly");
        auto after=v36_insertion(candidate);double phi=after.phi-before.phi;long long over=after.overflow-before.overflow;
        double w=v43_reference_wire(candidate),dw=w-old_wire;
        detail["overflow_delta"]=double(over);detail["potential_delta"]=phi;detail["reference_wire_delta_dbu"]=dw;
        auto vias=[&](const Route&r){int n=0;for(auto e:r.edges){int a,b,l,x,y,ll;decode(e.a,a,b,l);decode(e.b,x,y,ll);n+=l!=ll&&std::min(l,ll)!=10;}return n;};
        if(!std::isfinite(w)||wire+dw>limit+1e-6||w>old_wire*1.2+8*unit||vias(candidate)>vias(tx.old)+16){detail["reason"]="wire_or_via_budget";return false;}
        if(over>0||phi>1e-9||!(over<0||phi<-1e-8)){detail["reason"]="no_full_resource_gain";return false;}
        detail["accepted"]=true;detail["reason"]="pareto_improvement";
        tx.install(std::move(candidate));
#ifdef GR_V44_TEST
        if(v44_install_hook_)v44_install_hook_();
#endif
        wire+=dw;tx.committed=true;return true;
    }catch(const RerouteBudget&){detail["reason"]="time_budget";return false;}
    catch(const V3Budget&){detail["reason"]="search_or_topology_budget";return false;}
    catch(const RouterTimeout&){throw;}
    catch(const std::runtime_error&e){std::string s=e.what();if(s.find("No legal path")==0||s.find("A* expansion")==0||s.find("Disconnected route")==0||s.find("Nonunique TSV")==0){detail["reason"]="infeasible";return false;}throw;}
}
void Router::optimize_v44(){
    Progress::Scope log(progress,"V44_CONGESTION_NEGOTIATION");double began=elapsed();
    bool old_active=v44_active_;v44_active_=true;double old_deadline=reroute_deadline_seconds_;
    struct Reset{Router&r;bool active;double deadline;~Reset(){r.v44_active_=active;r.reroute_deadline_seconds_=deadline;}}reset{*this,old_active,old_deadline};
    double wire=0;size_t unknown=0;for(auto&r:routes){double w=v43_reference_wire(r);if(std::isfinite(w))wire+=w;else ++unknown;}
    double start_wire=wire,limit=wire*(1+v44.wire_growth);auto initial=batch_quality_full();
    std::unique_ptr<DensityCorrelation>density;if(v3_thermal_ready_){std::vector<int>counts(v3_thermal_rise_.size());for(auto&r:routes)if(r.cross)++counts[v3_thermal_cell(r.tsv)];density=std::make_unique<DensityCorrelation>(v3_thermal_rise_,std::move(counts));}
    const double rho_floor=density?density->value()-1e-5:0;const size_t resource_limit=resources.size()+200000;
    const double phase_end=began+150;Json rounds=Json::array();std::vector<unsigned char>failures(routes.size(),0);int stagnant=0;
    const int count=v44.negotiation?v44.rounds:1;
    for(int round=0;round<count;round++){
        check_time();if(elapsed()>=phase_end||resources.size()>resource_limit)break;
        v44_present_=std::min(4.,1.+round);auto prior=batch_quality_full();
        if(v44.negotiation){size_t entries=0;for(auto[v,r]:resources){if(++entries%4096==0)check_time();if(r.capacity<=0||!r.usage)continue;auto a=scoring_cell(v,r.usage),b=scoring_cell(v,0);if(a.overflow>b.overflow){auto h=v44_history_.find(v);if(h!=v44_history_.end())h->second=std::min(16.,h->second+std::min(2.,double(a.overflow-b.overflow)/r.capacity));else if(v44_history_.size()<200000)v44_history_.emplace(v,std::min(2.,double(a.overflow-b.overflow)/r.capacity));}}}
        std::vector<std::pair<double,size_t>>rank;rank.reserve(routes.size());
        for(size_t i=0;i<routes.size();i++){
            if(i%128==0)check_time();if(failures[i]>=2||routes[i].nodes.size()>10000)continue;double gain=0;
            for(auto[v,n]:v34_usage(routes[i])){auto&s=resource(v);if(s.capacity>0){auto a=scoring_cell(v,s.usage),b=scoring_cell(v,s.usage-n);gain+=a.overflow-b.overflow+32*(a.phi-b.phi);}}
            if(gain>0)rank.emplace_back(-gain,i);
        }std::sort(rank.begin(),rank.end());
        size_t attempts=0,accepted=0,tsv_accepted=0,patch_accepted=0;std::map<std::string,int>reasons;double t0=elapsed();
        for(auto[priority,i]:rank){(void)priority;if(attempts>=4096||elapsed()>=phase_end||resources.size()>resource_limit)break;check_time();bool improved=false;
            std::vector<std::pair<double,Id>>sites;
            if(v44.tsv&&routes[i].cross&&d.nets[i].pins.size()<=32&&routes[i].nodes.size()<=4096){
                int x,y,l;decode(routes[i].tsv,x,y,l);std::set<Id>unique;
                for(int radius:{1,2,4,8,16})for(int dx:{-radius,0,radius})for(int dy:{-radius,0,radius})if((dx||dy)&&x+dx>=0&&x+dx<nx&&y+dy>=0&&y+dy<ny)unique.insert(id(x+dx,y+dy,10));
                for(Id site:unique){if(!tsv_legal(site))continue;if(density&&density->after_move(v3_thermal_cell(routes[i].tsv),v3_thermal_cell(site))<rho_floor)continue;
                    Route fp;fp.cross=true;fp.tsv=site;try{prepare_v34_route(d.nets[i],fp);}catch(const std::runtime_error&){continue;}
                    double price=0;for(auto[v,n]:v34_usage(fp)){auto&s=resource(v);auto it=std::lower_bound(routes[i].resource_demand.begin(),routes[i].resource_demand.end(),v,[](auto p,Id key){return p.first<key;});int remove=it!=routes[i].resource_demand.end()&&it->first==v?it->second:0;auto a=scoring_cell(v,s.usage-remove),b=scoring_cell(v,s.usage-remove+n);price+=b.overflow-a.overflow+32*(b.phi-a.phi);auto h=v44_history_.find(v);if(h!=v44_history_.end())price+=h->second*n;}
                    int sx,sy,sl;decode(site,sx,sy,sl);price+=.02*(std::abs(sx-x)+std::abs(sy-y));sites.emplace_back(price,site);
                }std::sort(sites.begin(),sites.end());if(sites.size()>4)sites.resize(4);
            }
            for(auto[price,site]:sites){(void)price;if(attempts>=4096||elapsed()>=phase_end)break;Json detail;Id old=routes[i].tsv;++attempts;
                bool ok=v44_trial(i,0,site,wire,limit,detail);if(ok){if(density)density->move(v3_thermal_cell(old),v3_thermal_cell(site));++accepted;++tsv_accepted;improved=true;break;}++reasons[detail.at("reason").string()];}
            if(!improved&&(v44.layer||v44.negotiation||v44.cost)&&attempts<4096&&elapsed()<phase_end){
                std::map<Id,int>degree;for(auto e:routes[i].edges){++degree[e.a];++degree[e.b];}
                std::set<Id>keep(routes[i].pin_access_nodes.begin(),routes[i].pin_access_nodes.end());if(routes[i].cross){keep.insert(routes[i].tsv);keep.insert(routes[i].tsv+Id(nx)*ny);}
                Id seed=0;double best=0;for(auto[v,n]:v34_usage(routes[i]))if(routes[i].nodes.count(v)&&degree[v]==2&&!keep.count(v)){auto&s=resource(v);if(s.capacity<=0)continue;auto a=scoring_cell(v,s.usage),b=scoring_cell(v,s.usage-n);double gain=a.overflow-b.overflow+32*(a.phi-b.phi);if(gain>best||(gain==best&&gain>0&&v<seed)){seed=v;best=gain;}}
                if(seed){Json detail;++attempts;if(v44_trial(i,seed,0,wire,limit,detail)){++accepted;++patch_accepted;improved=true;}else ++reasons[detail.at("reason").string()];}else ++reasons["no_movable_branch"];
            }
            if(improved)failures[i]=0;else ++failures[i];if(progress&&attempts%32==0)progress->poll();
        }
        auto after=batch_quality_full();Json row=Json::object();row["round"]=round;row["attempted"]=double(attempts);row["accepted"]=double(accepted);row["tsv_accepted"]=double(tsv_accepted);row["patch_accepted"]=double(patch_accepted);row["overflow_delta"]=double(after.overflow-prior.overflow);row["potential_delta"]=after.phi-prior.phi;row["seconds"]=elapsed()-t0;row["reference_wire_dbu"]=wire;Json rejects=Json::object();for(auto[k,v]:reasons)rejects[k]=v;row["rejections"]=rejects;rounds.push(row);
        if(!accepted){if(++stagnant>=2)break;}else stagnant=0;
    }
    auto final=batch_quality_full();v44_stats_["rounds"]=rounds;v44_stats_["overflow_delta"]=double(final.overflow-initial.overflow);v44_stats_["potential_delta"]=final.phi-initial.phi;v44_stats_["wire_growth_budget"]=v44.wire_growth;v44_stats_["reference_wire_before_dbu"]=start_wire;v44_stats_["reference_wire_after_dbu"]=wire;v44_stats_["unmeasured_unchanged_nets"]=double(unknown);v44_stats_["history_entries"]=double(v44_history_.size());v44_stats_["elapsed_seconds"]=elapsed()-began;v44_stats_["official_metrics"]=false;v44_stats_["cost_enabled"]=v44.cost;v44_stats_["negotiation_enabled"]=v44.negotiation;v44_stats_["layer_enabled"]=v44.layer;v44_stats_["tsv_enabled"]=v44.tsv;
    reroute_deadline_seconds_=old_deadline;for(size_t i=0;i<routes.size();i++){if(i%128==0)check_time();validate(d.nets[i],routes[i]);}
}
}
