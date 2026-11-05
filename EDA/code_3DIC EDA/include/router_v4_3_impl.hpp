#pragma once
#if !defined(GR_V4) || !defined(GR_V4_2)
#error V4.3 requires all V4.2 legality branches
#endif
#ifdef __linux__
#include <sys/resource.h>
#endif
namespace gr {
// A search proxy, not an official score. Nonnegative edge toll preserves the
// geometric A* lower bound. The exact full-Net/group delta decides acceptance.
double Router::scoring_aware_congestion_cost(Id v,int demand){
    if(demand<0)throw std::runtime_error("Negative scoring-aware demand");
    if(demand==0)return 0;
    auto&r=resource(v);auto before=scoring_cell(v,r.usage);
    if(r.scoring_capacity<=0)return demand?std::numeric_limits<double>::infinity():0;
    if(std::binary_search(v34_active_pin_cells_.begin(),v34_active_pin_cells_.end(),v))return 0;
    auto after=scoring_cell(v,r.usage+demand);
    double utilization=std::min(1.0,double(r.scoring_fixed+r.usage+demand)/r.scoring_capacity);
    return 32*std::max(0.0,after.phi-before.phi)+.02*std::pow(utilization,4)+cfg.history/std::max(1.0,cfg.congestion)*r.history;
}
// Match the published REFERENCE_20261103 Guide wire proxy on positive-area
// grid-aligned Guides, including nonuniform cells. No reference formula edits.
double Router::v43_reference_wire(const Route&r)const{
    if(r.nodes.size()>100000||r.guides.size()>2048)return std::numeric_limits<double>::quiet_NaN();
    double cost=0;for(Id v:r.nodes){int x,y,l;decode(v,x,y,l);cost+=d.layers.at(l).direction=="HORIZONTAL"?xs[x+1]-xs[x]:ys[y+1]-ys[y];}
    std::array<std::vector<const Rect*>,21> layers;for(auto&g:r.guides)layers[g.layer].push_back(&g);
    for(int l=1;l<20;l++)if(l!=10)for(auto*a:layers[l])for(auto*b:layers[l+1])
        if(d.vias.count({l,l+1})&&std::min(a->x2,b->x2)>std::max(a->x1,b->x1)&&std::min(a->y2,b->y2)>std::max(a->y1,b->y1)){
            int x=locate(xs,(std::max(a->x1,b->x1)+std::min(a->x2,b->x2))/2),y=locate(ys,(std::max(a->y1,b->y1)+std::min(a->y2,b->y2))/2);
            cost+=3*std::max(xs[x+1]-xs[x],ys[y+1]-ys[y]);break;
        }
    if(r.cross){int x,y,l;decode(r.tsv,x,y,l);cost+=6*std::max(xs[x+1]-xs[x],ys[y+1]-ys[y]);}return cost;
}
struct Router::V43BatchTransaction {
    std::vector<std::unique_ptr<V36Transaction>> states;
    V43BatchTransaction(Router&r,const std::vector<size_t>&ids){states.reserve(ids.size());for(size_t i:ids)states.push_back(std::make_unique<V36Transaction>(r,i));}
    ~V43BatchTransaction(){for(auto i=states.rbegin();i!=states.rend();++i)(*i)->rollback();}
    void commit()noexcept{for(auto&s:states)s->committed=true;}
};
Route Router::v43_local_patch(size_t i,const Route&old,Id seed){
    std::map<Id,std::vector<Id>>adj;for(auto e:old.edges){adj[e.a].push_back(e.b);adj[e.b].push_back(e.a);}
    std::set<Id>keep(old.pin_access_nodes.begin(),old.pin_access_nodes.end());
    if(keep.empty())for(auto&p:d.nets[i].pins)keep.insert(pin_node(p));
    if(old.cross){keep.insert(old.tsv);keep.insert(old.tsv+Id(nx)*ny);}
    if(keep.count(seed)||adj[seed].size()!=2)throw V3Budget();
    std::set<Id>removed{seed};std::vector<Edge>erased;
    auto walk=[&](Id at){Id prev=seed;erased.push_back({std::min(prev,at),std::max(prev,at)});
        for(int k=0;k<8;k++){if(keep.count(at)||adj[at].size()!=2||removed.count(at))break;
            removed.insert(at);Id next=adj[at][0]==prev?adj[at][1]:adj[at][0];prev=at;at=next;erased.push_back({std::min(prev,at),std::max(prev,at)});
        }return at;};
    Id a=walk(adj[seed][0]),b=walk(adj[seed][1]);if(a==b||removed.count(a)||removed.count(b))throw V3Budget();
    Route out=old;for(auto v:removed)out.nodes.erase(v);for(auto e:erased)out.edges.erase(e);
    int ax,ay,al,bx,by,bl;decode(a,ax,ay,al);decode(b,bx,by,bl);if((al<=10)!=(bl<=10))throw V3Budget();
    v4_xl_=std::max(0,std::min(ax,bx)-4);v4_xh_=std::min(nx-1,std::max(ax,bx)+4);
    v4_yl_=std::max(0,std::min(ay,by)-4);v4_yh_=std::min(ny-1,std::max(ay,by)+4);
    int lo=std::max(al<=10?1:11,std::min(al,bl)-2),hi=std::min(al<=10?10:20,std::max(al,bl)+2);
    auto p=search(a,b,lo,hi,old.cross);for(Id v:p)out.nodes.insert(v);for(size_t k=1;k<p.size();k++)out.edges.insert({std::min(p[k-1],p[k]),std::max(p[k-1],p[k])});
    out.steiner_points.erase(std::remove_if(out.steiner_points.begin(),out.steiner_points.end(),[&](Id v){return !out.nodes.count(v);}),out.steiner_points.end());return out;
}
bool Router::v43_try_group(const std::vector<size_t>&group,const std::vector<Id>&seeds,double&wire,double limit,Json&detail){
    detail=Json::object();detail["accepted"]=false;
    if(group.size()<2||group.size()>3||group.size()!=seeds.size()){detail["reason"]="group_size";return false;}
    if(std::set<size_t>(group.begin(),group.end()).size()!=group.size())throw std::runtime_error("Duplicate V4.3 group Net");
    size_t nodes=0;double old_wire=0,old_reference=0;std::map<Id,std::pair<int,int>>demand;
    for(size_t i:group){if(i>=routes.size())throw std::runtime_error("Invalid V4.3 Net index");nodes+=routes[i].nodes.size();
        double w=guide_grid_cost(routes[i]),ref=v43_reference_wire(routes[i]);if(nodes>20000||!std::isfinite(w)||!std::isfinite(ref)){detail["reason"]="group_workspace";return false;}old_wire+=w;old_reference+=ref;
        for(auto[v,n]:v34_usage(routes[i]))demand[v].first+=n;
    }
    bool active=v4_search_active_,reroute=rerouting_;double deadline=reroute_deadline_seconds_;auto pins=std::move(v34_active_pin_cells_);
    struct Reset{Router&r;bool a,b;double deadline;std::vector<Id>&pins;~Reset(){r.v4_search_active_=a;r.rerouting_=b;r.reroute_deadline_seconds_=deadline;r.v34_active_pin_cells_=std::move(pins);}}reset{*this,active,reroute,deadline,pins};
    v4_search_active_=true;rerouting_=true;reroute_deadline_seconds_=deadline>0?std::min(deadline,elapsed()+.35):elapsed()+.35;
    V43BatchTransaction transaction(*this,group);double next_wire=0,next_reference=0;
    try{
        for(size_t k=0;k<group.size();k++){
            check_time();size_t i=group[k];const auto&old=transaction.states[k]->old;v34_active_pin_cells_=own_pin_cells(d.nets[i]);
            Route candidate;
#ifdef GR_V43_TEST
            if(v43_candidate_hook_)candidate=v43_candidate_hook_(i,old,seeds[k]);else
#endif
            candidate=v43_local_patch(i,old,seeds[k]);
            candidate.guides.clear();make_guides(candidate);prepare_v34_route(d.nets[i],candidate);validate(d.nets[i],candidate);
            if(candidate.pin_access_nodes!=old.pin_access_nodes||candidate.tsv!=old.tsv)throw std::runtime_error("V4.3 group changed fixed Pin/TSV access");
            double w=guide_grid_cost(candidate),ref=v43_reference_wire(candidate);
            if(!std::isfinite(w)||!std::isfinite(ref)){detail["reason"]="candidate_workspace";return false;}
            auto vias=[&](const Route&t){int count=0;for(auto e:t.edges){int x,y,l,xx,yy,ll;decode(e.a,x,y,l);decode(e.b,xx,yy,ll);count+=l!=ll&&std::min(l,ll)!=10;}return count;};
            if(vias(candidate)>vias(old)+8){detail["reason"]="group_via_guard";return false;}next_wire+=w;next_reference+=ref;
            for(auto[v,n]:v34_usage(candidate))demand[v].second+=n;
            transaction.states[k]->install(std::move(candidate));
#ifdef GR_V43_TEST
            if(v43_install_hook_)v43_install_hook_();
#endif
        }
        double phi=0;long long over=0;for(auto[v,n]:demand){auto&s=resource(v);auto a=scoring_cell(v,s.usage-n.second+n.first),b=scoring_cell(v,s.usage);phi+=b.phi-a.phi;over+=b.overflow-a.overflow;}
        double dw=next_wire-old_wire,dr=next_reference-old_reference;
        detail["potential_delta"]=phi;detail["overflow_delta"]=double(over);detail["wire_delta_dbu"]=dw;detail["reference_wire_delta_dbu"]=dr;
        if(wire+dw>limit+1e-6||(v43_active_&&v43_wire_+dr>v43_wire_limit_+1e-6)||next_wire>old_wire*1.02+16*unit){detail["reason"]="group_wire_guard";return false;}
        if(phi>=-1e-9||over>0){detail["reason"]="group_quality_guard";return false;}
        // All throwing report/trace work precedes the no-allocation commit.
        detail["accepted"]=true;detail["reason"]="cooperative_improvement";detail["nets"]=numbers(group);trace.event("v43_group_committed",detail);
        wire+=dw;if(v43_active_)v43_wire_+=dr;transaction.commit();return true;
    }catch(const RouterTimeout&){throw;}
    catch(const RerouteBudget&){detail["reason"]="group_time_budget";return false;}
    catch(const V3Budget&){detail["reason"]="group_search_budget";return false;}
    catch(const std::runtime_error&e){std::string m=e.what();if(m.find("No legal path")==0||m.find("A* expansion")==0||m.find("Disconnected route")==0){detail["reason"]="infeasible_group_patch";return false;}throw;}
}
static double v43_peak_rss(){
#ifdef __linux__
    rusage u{};return getrusage(RUSAGE_SELF,&u)==0?double(u.ru_maxrss)*1024:0;
#else
    return 0;
#endif
}
void Router::v43_multinet(double&wire,double limit){
    Progress::Scope log(progress,"V43_COOPERATIVE_REROUTE");double began=elapsed();auto before=batch_quality_full();
    std::priority_queue<std::pair<double,Id>>top;size_t checked=0;
    for(auto[v,s]:resources){if(++checked%4096==0)check_time();if(!s.usage||s.capacity<=0)continue;auto a=scoring_cell(v,s.usage),b=scoring_cell(v,0);if(a.phi>b.phi){std::pair<double,Id>entry{b.phi-a.phi,v};if(top.size()<256)top.push(entry);else if(entry<top.top()){top.pop();top.push(entry);}}}
    std::vector<std::pair<double,Id>>ranked;while(!top.empty()){ranked.push_back(top.top());top.pop();}std::sort(ranked.begin(),ranked.end());
    std::unordered_map<Id,std::vector<size_t>>owners;for(auto[p,v]:ranked)owners.emplace(v,std::vector<size_t>{});
    size_t scanned=0;bool truncated=false;for(size_t i=0;i<routes.size()&&!truncated;i++){
        if(i%128==0)check_time();if(routes[i].nodes.size()>10000)continue;
        for(auto[v,n]:v34_usage(routes[i])){if(++scanned>4000000){truncated=true;break;}auto at=owners.find(v);if(at!=owners.end()&&at->second.size()<8&&routes[i].nodes.count(v))at->second.push_back(i);}
    }
    double prior=reroute_deadline_seconds_;reroute_deadline_seconds_=began+8;
    struct Reset{double&x;double old;~Reset(){x=old;}}reset{reroute_deadline_seconds_,prior};
    std::set<std::vector<size_t>>seen;std::vector<unsigned char>visits(routes.size(),0);size_t attempts=0,accepted=0;std::map<std::string,int>reasons;double candidate_seconds=0,max_candidate=0;
    for(auto[p,hot]:ranked){(void)p;if(attempts>=32||elapsed()>=reroute_deadline_seconds_)break;
        std::vector<size_t>group;for(size_t i:owners.at(hot))if(visits[i]<2)group.push_back(i);
        std::sort(group.begin(),group.end(),[&](size_t a,size_t b){return routes[a].nodes.size()!=routes[b].nodes.size()?routes[a].nodes.size()<routes[b].nodes.size():a<b;});if(group.size()>3)group.resize(3);if(group.size()<2)continue;
        auto key=group;std::sort(key.begin(),key.end());if(!seen.insert(key).second)continue;for(size_t i:group)++visits[i];
        for(int order=0;order<2&&attempts<32;order++){
            std::vector<Id>seeds;for(auto i:group){Id seed=hot;std::set<Id>mandatory(routes[i].pin_access_nodes.begin(),routes[i].pin_access_nodes.end());std::map<Id,int>degree;for(auto e:routes[i].edges){++degree[e.a];++degree[e.b];}
                auto interface=[&](Id v){return routes[i].cross&&(v==routes[i].tsv||v==routes[i].tsv+Id(nx)*ny);};
                if(mandatory.count(seed)||degree[seed]!=2||interface(seed)){seed=0;double best=0;for(auto[v,n]:v34_usage(routes[i]))if(routes[i].nodes.count(v)&&degree[v]==2&&!mandatory.count(v)&&!interface(v)){auto&s=resource(v);double gain=scoring_cell(v,s.usage).phi-scoring_cell(v,s.usage-n).phi;if(gain>best){best=gain;seed=v;}}}seeds.push_back(seed);
            }
            if(std::find(seeds.begin(),seeds.end(),0)!=seeds.end()){++reasons["no_movable_local_seed"];break;}
            ++attempts;Json detail;double t=elapsed();bool ok=v43_try_group(group,seeds,wire,limit,detail);double duration=elapsed()-t;candidate_seconds+=duration;max_candidate=std::max(max_candidate,duration);
            if(ok){++accepted;break;}++reasons[detail.at("reason").string()];std::reverse(group.begin(),group.end());
        }
    }
    reroute_deadline_seconds_=prior;auto after=batch_quality_full();Json stats=Json::object();stats["attempted"]=double(attempts);stats["accepted"]=double(accepted);stats["overflow_delta"]=double(after.overflow-before.overflow);stats["potential_delta"]=after.phi-before.phi;
    stats["owner_scan_entries"]=double(scanned);stats["owner_scan_truncated"]=truncated;stats["indexed_hotspots"]=double(ranked.size());stats["elapsed_seconds"]=elapsed()-began;stats["candidate_seconds"]=candidate_seconds;stats["max_candidate_seconds"]=max_candidate;stats["max_trials"]=32;stats["max_group_size"]=3;stats["phase_seconds"]=8;stats["process_peak_rss_bytes"]=v43_peak_rss();
    Json r=Json::object();for(auto[k,v]:reasons)r[k]=v;stats["rejection_reasons"]=r;v43_stats_["multinet"]=stats;
}
void Router::v43_tsv_refine(bool thermal,double&wire,double limit,DensityCorrelation*density){
    Progress::Scope log(progress,thermal?"V43_THERMAL_JOINT":"V43_ADAPTIVE_TSV");double began=elapsed(),wire_before=wire,ref_before=v43_wire_;auto before=batch_quality_full();
    // Zero-capacity fixed tokens remain in the resource model, but their
    // legacy sentinel must never inflate a positive-capacity tradeoff budget.
    for(auto[v,s]:resources)if(s.capacity<=0)before.phi-=scoring_cell(v,s.usage).phi;
    double rho0=density?density->value():0;
    std::map<Id,int>counts;for(auto&r:routes)if(r.cross)++counts[r.tsv];
    using Entry=std::pair<double,size_t>;using Queue=std::priority_queue<Entry,std::vector<Entry>,std::greater<Entry>>;
    std::map<std::pair<int,int>,Queue>regions;
    auto impact=[&](size_t i){double score=0;for(auto[v,n]:v34_usage(routes[i])){auto&s=resource(v);if(s.capacity>0)score+=scoring_cell(v,s.usage).phi-scoring_cell(v,s.usage-n).phi;}return score;};
    for(size_t i=0;i<routes.size();i++)if(routes[i].cross&&routes[i].nodes.size()<=100000){if(i%128==0)check_time();int x,y,l;decode(routes[i].tsv,x,y,l);regions[{x/32,y/32}].push({-impact(i)-.001*std::log1p(counts.at(routes[i].tsv)),i});}
    double prior=reroute_deadline_seconds_;reroute_deadline_seconds_=began+(thermal?120:180);struct Reset{double&x;double old;~Reset(){x=old;}}reset{reroute_deadline_seconds_,prior};
    size_t attempts=0,moved=0,visited=0,extra=0,prechecked=0;long long used_over=0;double used_phi=0,trial_seconds=0,max_trial=0;
    const size_t max_trials=thermal?12000:16000,resource_limit=resources.size()+300000;const long long over_budget=static_cast<long long>(std::floor(before.overflow*.0001));const double phi_budget=before.phi*.0001;
    std::map<std::string,int>reasons;std::string stop="regions_exhausted";
    while(attempts<max_trials){
        std::vector<std::pair<double,std::pair<int,int>>>order;
        for(auto&[region,q]:regions)if(!q.empty())order.push_back({q.top().first,region});if(order.empty())break;std::sort(order.begin(),order.end());
        bool halt=false;for(auto[weight,region]:order){(void)weight;
            if(elapsed()>=reroute_deadline_seconds_||resources.size()>resource_limit||attempts>=max_trials){stop=attempts>=max_trials?"trial_budget":resources.size()>resource_limit?"resource_budget":"phase_time_budget";halt=true;break;}
            auto&q=regions.at(region);std::vector<Entry>front;for(int k=0;k<4&&!q.empty();k++){auto i=q.top().second;q.pop();front.push_back({-impact(i)-.001*std::log1p(counts.at(routes[i].tsv)),i});}std::sort(front.begin(),front.end());size_t i=front[0].second;for(size_t k=1;k<front.size();k++)q.push(front[k]);++visited;
            int x,y,l;decode(routes[i].tsv,x,y,l);double severity=-front[0].first;
            size_t budget=severity>=1?6:severity>=.1?4:2;if(routes[i].nodes.size()>4096)budget=std::min<size_t>(budget,2);
            std::set<Id>sites;for(int r:{1,2,4,8,16,32})for(int dx:{-r,0,r})for(int dy:{-r,0,r})if((dx||dy)&&x+dx>=0&&x+dx<nx&&y+dy>=0&&y+dy<ny&&(!thermal||std::abs(dx)+std::abs(dy)<=4)){Id site=id(x+dx,y+dy,10);if(tsv_legal(site))sites.insert(site);else++prechecked;}
            std::vector<Entry>candidates;for(Id site:sites){
                if(thermal&&density&&density->counts()[v3_thermal_cell(site)]>=density->counts()[v3_thermal_cell(routes[i].tsv)]){++prechecked;continue;}
                double dr=density?density->after_move(v3_thermal_cell(routes[i].tsv),v3_thermal_cell(site))-density->value():0;if((thermal&&dr<=1e-12)||(!thermal&&density&&density->value()+dr<rho0-1e-5)){++prechecked;continue;}
                Route f;f.cross=true;f.tsv=site;prepare_v34_route(d.nets[i],f);double price=0;for(auto[v,n]:v34_usage(f)){auto&s=resource(v);auto it=std::lower_bound(routes[i].resource_demand.begin(),routes[i].resource_demand.end(),v,[](auto a,Id b){return a.first<b;});int removed=it!=routes[i].resource_demand.end()&&it->first==v?it->second:0;auto a=scoring_cell(v,s.usage-removed),b=scoring_cell(v,s.usage-removed+n);price+=b.phi-a.phi;}
                int sx,sy,sl;decode(site,sx,sy,sl);double distance=std::abs(sx-x)+std::abs(sy-y);auto at=counts.find(site);int count=at==counts.end()?0:at->second;
                candidates.push_back({thermal?-dr/(1+.05*distance):price+.0001*distance+.001*count,site});
            }
            std::sort(candidates.begin(),candidates.end());if(candidates.size()>budget)candidates.resize(budget);size_t rejects_wire=0,k=0;
            for(auto[price,site]:candidates){(void)price;if(attempts>=max_trials)break;++attempts;if(++k>2)++extra;Id old=routes[i].tsv;auto target=counts.try_emplace(site,0).first;Json detail;double t=elapsed();
                bool ok=v4_try(i,site,thermal,wire,limit,density,thermal?density->value()+1e-12:rho0-1e-5,over_budget-used_over,thermal?phi_budget-used_phi:0,detail);double duration=elapsed()-t;trial_seconds+=duration;max_trial=std::max(max_trial,duration);
                if(ok){++moved;--counts.at(old);++target->second;used_over+=static_cast<long long>(detail.at("overflow_delta").number());used_phi+=detail.at("potential_delta").number();break;}
                auto reason=detail.at("reason").string();++reasons[reason];if(reason=="reference_wire_guard"||reason=="wire_or_via_guard"){if(++rejects_wire>=2){++reasons["adaptive_wire_cooldown"];break;}}if(reason=="local_search_or_topology_budget")break;
            }
            if(progress&&visited%64==0)progress->poll();
        }if(halt)break;
    }
    reroute_deadline_seconds_=prior;auto after=batch_quality_full();for(auto[v,r]:resources)if(r.capacity<=0)after.phi-=scoring_cell(v,r.usage).phi;
    Json s=Json::object();s["attempted"]=double(attempts);s["accepted"]=double(moved);s["visited_nets"]=double(visited);s["extra_candidates_attempted"]=double(extra);s["presearch_rejections"]=double(prechecked);s["overflow_delta"]=double(after.overflow-before.overflow);s["potential_delta"]=after.phi-before.phi;s["rho_delta"]=density?density->value()-rho0:0;s["wire_delta_dbu"]=wire-wire_before;s["reference_wire_delta_dbu"]=v43_wire_-ref_before;s["candidate_seconds"]=trial_seconds;s["max_candidate_seconds"]=max_trial;s["elapsed_seconds"]=elapsed()-began;s["max_trials"]=double(max_trials);s["phase_seconds"]=thermal?120:180;s["candidate_limit_max"]=6;s["overflow_budget"]=double(over_budget);s["positive_capacity_potential_budget"]=phi_budget;s["process_peak_rss_bytes"]=v43_peak_rss();s["stop_reason"]=stop;Json rejects=Json::object();for(auto[k,n]:reasons)rejects[k]=n;s["rejection_reasons"]=rejects;v43_stats_[thermal?"thermal_joint":"adaptive_tsv"]=s;
}
void Router::optimize_v43(){
    Progress::Scope log(progress,"V43_SCORING_AWARE_OPTIMIZATION");v43_stats_["official_metrics"]=false;v43_stats_["ruleset"]="REFERENCE_20261103 proxy";
    bool prior=v43_active_;v43_active_=true;struct Reset{bool&b;bool old;~Reset(){b=old;}}reset{v43_active_,prior};
    double wire=0;v43_wire_=0;size_t unknown=0;for(auto&r:routes){double a=guide_grid_cost(r),b=v43_reference_wire(r);if(std::isfinite(a)&&std::isfinite(b)){wire+=a;v43_wire_+=b;}else ++unknown;}
    // Unmeasurable large Nets remain unchanged. All candidates are measured.
    double original=wire,ref_original=v43_wire_,limit=wire*1.0049;v43_wire_limit_=v43_wire_*1.0049;
    v43_stats_["unmeasured_unchanged_nets"]=double(unknown);v43_stats_["wire_growth_fraction"]=.0049;
#ifndef GR_V43_DISABLE_SCORING
    v43_stats_["scoring_aware"]=true;
#else
    v43_stats_["scoring_aware"]=false;
#endif
    // Reserve wire budget for TSV/thermal rather than letting the first
    // module exhaust the entire allowance (observed in frozen V4 ablations).
    v43_wire_limit_=ref_original*1.0015;v4_layer_escape(wire,std::min(limit,original*1.0015));v43_wire_limit_=ref_original*1.0049;
#ifndef GR_V43_DISABLE_MULTINET
    v43_multinet(wire,limit);
#endif
    std::unique_ptr<DensityCorrelation>density;if(v3_thermal_ready_){std::vector<int>bins(v3_thermal_rise_.size());for(auto&r:routes)if(r.cross)++bins[v3_thermal_cell(r.tsv)];density=std::make_unique<DensityCorrelation>(v3_thermal_rise_,std::move(bins));}
#ifndef GR_V43_DISABLE_ADAPTIVE
    v43_tsv_refine(false,wire,limit,density.get());
#else
    v4_tsv_refine(false,wire,limit,density.get());
#endif
    if(density){
#ifndef GR_V43_DISABLE_THERMAL
        v43_tsv_refine(true,wire,limit,density.get());
#else
        v4_tsv_refine(true,wire,limit,density.get());
#endif
    }
    v43_stats_["wire_delta_dbu"]=wire-original;v43_stats_["reference_wire_delta_dbu"]=v43_wire_-ref_original;v43_stats_["process_peak_rss_bytes"]=v43_peak_rss();
    for(size_t i=0;i<routes.size();i++){if(i%128==0)check_time();validate(d.nets[i],routes[i]);}
}
}
