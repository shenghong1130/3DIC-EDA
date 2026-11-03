#pragma once
#if !defined(GR_V3_FINAL) || !defined(GR_V3_5) || !defined(GR_V3_6)
#error V4 requires the frozen V3.6 routing branches
#endif
namespace gr {
// Same complete-Net resource accounting as V3.6. Only one bounded Net is
// backed up, never the Route collection or resource graph. Undo allocates no
// memory: all old resource keys and cached physical demand are retained.
struct Router::V4Transaction {
    V36Transaction state;
    V4Transaction(Router&r,size_t i):state(r,i){}
    const Route&old()const{return state.old;}
    void install(Route r){state.install(std::move(r));}
    void commit()noexcept{state.committed=true;}
};

Json Router::v4_attribution()const{
    long long fixed_over=0,total=0,increment=0,mandatory=0;size_t hotspots=0,zero=0;
    for(auto[v,s]:resources){int x,y,l;decode(v,x,y,l);if(l!=1)continue;
        long long f=std::max(0,s.fixed-s.capacity),t=std::max(0,s.fixed+s.usage-s.capacity);
        fixed_over+=f;total+=t;increment+=t-f;hotspots+=t>0;zero+=s.capacity==0&&s.fixed+s.usage>0;
    }
    for(const auto&r:routes)for(Id v:r.pin_access_nodes){int x,y,l;decode(v,x,y,l);mandatory+=l==1;}
    Json j=Json::object();j["fixed_baseline_overflow"]=double(fixed_over);j["total_overflow"]=double(total);
    j["routing_incremental_overflow"]=double(increment);j["hotspot_cells"]=double(hotspots);j["zero_capacity_demand_cells"]=double(zero);
    j["required_m1_pin_accesses"]=double(mandatory);j["scope"]="unchanged announcement resource model; not official attribution";return j;
}

Route Router::v4_local_escape(size_t i,const Route&old,Id seed){
    const auto&net=d.nets[i];std::map<Id,std::vector<Id>> adj;
    for(auto e:old.edges){adj[e.a].push_back(e.b);adj[e.b].push_back(e.a);}
    std::set<Id> keep(old.pin_access_nodes.begin(),old.pin_access_nodes.end());
    if(keep.empty())for(auto&p:net.pins)keep.insert(pin_node(p));
    if(old.cross){keep.insert(old.tsv);keep.insert(old.tsv+Id(nx)*ny);}
    if(keep.count(seed)||adj[seed].size()!=2)throw V3Budget();
    std::set<Id> removed{seed};std::vector<Edge> erased;
    auto walk=[&](Id next){Id prev=seed,at=next;erased.push_back({std::min(prev,at),std::max(prev,at)});
        for(int steps=0;steps<8;steps++){
            int x,y,l;decode(at,x,y,l);
            if(keep.count(at)||adj[at].size()!=2||l!=1||removed.count(at))break;
            removed.insert(at);Id dest=adj[at][0]==prev?adj[at][1]:adj[at][0];prev=at;at=dest;
            erased.push_back({std::min(prev,at),std::max(prev,at)});
        }return at;
    };
    Id a=walk(adj[seed][0]),b=walk(adj[seed][1]);if(a==b||removed.count(a)||removed.count(b))throw V3Budget();
    Route result=old;for(Id v:removed)result.nodes.erase(v);for(auto e:erased)result.edges.erase(e);
    int ax,ay,al,bx,by,bl;decode(a,ax,ay,al);decode(b,bx,by,bl);
    v4_xl_=std::max(0,std::min(ax,bx)-6);v4_xh_=std::min(nx-1,std::max(ax,bx)+6);
    v4_yl_=std::max(0,std::min(ay,by)-6);v4_yh_=std::min(ny-1,std::max(ay,by)+6);
    // A bounded M1/M2/M3 escape, not an unconstrained full-Net reroute.
    auto path=search(a,b,1,3,false);
    for(Id v:path)result.nodes.insert(v);for(size_t k=1;k<path.size();k++)result.edges.insert({std::min(path[k-1],path[k]),std::max(path[k-1],path[k])});
    result.steiner_points.erase(std::remove_if(result.steiner_points.begin(),result.steiner_points.end(),[&](Id v){return !result.nodes.count(v);}),result.steiner_points.end());
    return result;
}

Route Router::v4_local_tsv(size_t i,const Route&old,Id site){
    std::map<Id,std::vector<Id>> adj;
    Edge cross{old.tsv,old.tsv+Id(nx)*ny};
    for(auto e:old.edges)if(e.a!=cross.a||e.b!=cross.b){adj[e.a].push_back(e.b);adj[e.b].push_back(e.a);}
    std::set<Id> keep(old.pin_access_nodes.begin(),old.pin_access_nodes.end());
    if(keep.empty())for(auto&p:d.nets[i].pins)keep.insert(pin_node(p));
    Route result=old;result.edges.erase(cross);
    auto tail=[&](Id start){Id prev=start,at=start;
        if(keep.count(at)||adj[at].size()!=1)throw V3Budget();
        for(int steps=0;steps<8;steps++){
            Id next=adj[at][0]==prev&&adj[at].size()>1?adj[at][1]:adj[at][0];
            result.nodes.erase(at);result.edges.erase({std::min(at,next),std::max(at,next)});prev=at;at=next;
            if(keep.count(at)||adj[at].size()!=2)break;
        }return at;
    };
    Id low=tail(old.tsv),high=tail(old.tsv+Id(nx)*ny);result.tsv=site;
    int sx,sy,sl;decode(site,sx,sy,sl);int lx,ly,ll,hx,hy,hl;decode(low,lx,ly,ll);decode(high,hx,hy,hl);
    v4_xl_=std::max(0,std::min({sx,lx,hx})-6);v4_xh_=std::min(nx-1,std::max({sx,lx,hx})+6);
    v4_yl_=std::max(0,std::min({sy,ly,hy})-6);v4_yh_=std::min(ny-1,std::max({sy,ly,hy})+6);
    if(v4_xh_-v4_xl_>96||v4_yh_-v4_yl_>96)throw V3Budget();
    auto join=[&](Id from,Id to,int lo,int hi){auto path=search(from,to,lo,hi,true);for(Id v:path)result.nodes.insert(v);for(size_t k=1;k<path.size();k++)result.edges.insert({std::min(path[k-1],path[k]),std::max(path[k-1],path[k])});};
    join(site,low,1,10);join(site+Id(nx)*ny,high,11,20);
    result.nodes.insert(site);result.nodes.insert(site+Id(nx)*ny);result.edges.insert({site,site+Id(nx)*ny});
    result.steiner_points.erase(std::remove_if(result.steiner_points.begin(),result.steiner_points.end(),[&](Id v){return !result.nodes.count(v);}),result.steiner_points.end());return result;
}

bool Router::v4_try(size_t i,Id target,bool thermal_stage,double&wire,double limit,DensityCorrelation*density,
                    double rho_floor,long long over_allow,double phi_allow,Json&detail){
    detail=Json::object();detail["accepted"]=false;detail["reason"]="not_built";
    if(i>=routes.size()||routes[i].nodes.size()>100000){detail["reason"]="single_net_workspace";return false;}
    bool tsv=target&&routes[i].cross; // Layer Escape passes a real M1 seed.
    int x,y,l;decode(target,x,y,l);tsv=tsv&&l==10;
    if(tsv&&(!tsv_legal(target)||target==routes[i].tsv)){detail["reason"]="illegal_or_unchanged_site";return false;}
    double old_wire=guide_grid_cost(routes[i]);if(!std::isfinite(old_wire)){detail["reason"]="wire_workspace";return false;}
    double next_rho=density?density->value():0;size_t from=0,to=0;
    if(tsv&&density){from=v3_thermal_cell(routes[i].tsv);to=v3_thermal_cell(target);next_rho=density->after_move(from,to);
        if(next_rho+1e-12<rho_floor||(thermal_stage&&next_rho<=density->value()+1e-12)){detail["reason"]="thermal_guard";return false;}
        if(thermal_stage&&density->counts()[to]>=density->counts()[from]){detail["reason"]="density_guard";return false;}}
    const bool prior_active=v4_search_active_,prior_rerouting=rerouting_;const double prior_deadline=reroute_deadline_seconds_;
    auto pins=std::move(v34_active_pin_cells_);
    struct Context {Router&r;bool active,reroute;double deadline;std::vector<Id>&pins;
        ~Context(){r.v4_search_active_=active;r.rerouting_=reroute;r.reroute_deadline_seconds_=deadline;r.v34_active_pin_cells_=std::move(pins);}}
        context{*this,prior_active,prior_rerouting,prior_deadline,pins};
    v34_active_pin_cells_=own_pin_cells(d.nets[i]);
    v4_search_active_=true;rerouting_=true;reroute_deadline_seconds_=prior_deadline>0?std::min(prior_deadline,elapsed()+.25):elapsed()+.25;
    V4Transaction tx(*this,i);auto previous=v36_insertion(tx.old());
    Route candidate;
    try{
        candidate=tsv?v4_local_tsv(i,tx.old(),target):v4_local_escape(i,tx.old(),target);
        candidate.guides.clear();make_guides(candidate);prepare_v34_route(d.nets[i],candidate);validate(d.nets[i],candidate);
    }catch(const RouterTimeout&){throw;}
    catch(const RerouteBudget&){detail["reason"]="candidate_time_budget";return false;}
    catch(const V3Budget&){detail["reason"]="local_search_or_topology_budget";return false;}
    catch(const std::runtime_error&e){std::string message=e.what();
        if(message.find("No legal path")==0||message.find("A* expansion")==0||message.find("Nonunique TSV")==0||message.find("Disconnected route")==0){detail["reason"]="infeasible_local_patch";return false;}throw;
    }
    auto after=v36_insertion(candidate);long long delta=after.overflow-previous.overflow;double phi=after.phi-previous.phi;
    double cost=guide_grid_cost(candidate),dw=cost-old_wire;
    auto vias=[&](const Route&r){int n=0;for(auto e:r.edges){int a,b,c,x,y,z;decode(e.a,a,b,c);decode(e.b,x,y,z);n+=c!=z&&std::min(c,z)!=10;}return n;};
    int dv=vias(candidate)-vias(tx.old());
    detail["overflow_delta"]=double(delta);detail["potential_delta"]=phi;detail["wire_delta_dbu"]=dw;detail["via_delta"]=dv;
    detail["rho_delta"]=density?next_rho-density->value():0;
    if(!std::isfinite(cost)||wire+dw>limit+1e-6||cost>old_wire*1.02+16*unit||dv>8){detail["reason"]="wire_or_via_guard";return false;}
    if(!tsv){long long a=0,b=0;
        for(auto[v,n]:v34_usage(tx.old())){int x,y,l;decode(v,x,y,l);if(l==1){auto&s=resource(v);a+=scoring_cell(v,s.usage+n).overflow-scoring_cell(v,s.usage).overflow;}}
        for(auto[v,n]:v34_usage(candidate)){int x,y,l;decode(v,x,y,l);if(l==1){auto&s=resource(v);b+=scoring_cell(v,s.usage+n).overflow-scoring_cell(v,s.usage).overflow;}}
        detail["m1_overflow_delta"]=double(b-a);if(b>=a){detail["reason"]="no_m1_incremental_improvement";return false;}
    }
    if(thermal_stage){if(delta>over_allow||phi>phi_allow+1e-9){detail["reason"]="thermal_pareto_budget";return false;}}
    else if(delta>0||phi>1e-9||!(delta<0||phi< -1e-8)){detail["reason"]="no_joint_congestion_improvement";return false;}
    detail["accepted"]=true;detail["reason"]=thermal_stage?"thermal_pareto_move":"congestion_pareto_move";
    tx.install(std::move(candidate));
#ifdef GR_V4_TEST
    if(v4_install_hook_)v4_install_hook_();
#endif
    if(v4_inject_install_)throw std::runtime_error("Injected V4 interruption after install");
    // No allocation follows density mutation; all potentially throwing report
    // writes and install preflights precede the atomic commit boundary.
    if(tsv&&density)density->move(from,to);wire+=dw;tx.commit();return true;
}

void Router::v4_layer_escape(double&wire,double limit){
    Progress::Scope log(progress,"V4_LAYER_ESCAPE");const double began=elapsed();Json stats=Json::object();
    std::vector<std::pair<double,size_t>> ranked;size_t high=0;
    for(size_t i=0;i<routes.size();i++){
        if(i%256==0)check_time();double impact=0;
        for(auto[v,n]:v34_usage(routes[i])){int x,y,l;decode(v,x,y,l);if(l!=1)continue;auto&s=resource(v);impact+=scoring_cell(v,s.usage).overflow-scoring_cell(v,s.usage-n).overflow;}
        if(impact>0){ranked.emplace_back(-impact,i);high+=d.nets[i].pins.size()>32;}
    }
    std::sort(ranked.begin(),ranked.end());long long accepted=0,attempted=0,high_tried=0,high_accepted=0;std::map<std::string,int> rejected;
    const size_t resource_limit=resources.size()+200000;double deadline=reroute_deadline_seconds_;reroute_deadline_seconds_=began+90;
    struct Reset {double&value;double old;~Reset(){value=old;}}reset{reroute_deadline_seconds_,deadline};
    auto before=v4_attribution();
    // Reserve every fourth slot for high-Pin Nets: do not simply remove the
    // old Pin-count limit and expose an unbounded full-Net search.
    std::vector<size_t>small,large;for(auto[k,i]:ranked)(d.nets[i].pins.size()>32?large:small).push_back(i);
    size_t si=0,li=0;while(attempted<2048&&(si<small.size()||li<large.size())){
        if(elapsed()>=reroute_deadline_seconds_||resources.size()>resource_limit)break;
        size_t i;if(li<large.size()&&(attempted%4==0||si==small.size()))i=large[li++];else i=small[si++];
        std::set<Id> mandatory(routes[i].pin_access_nodes.begin(),routes[i].pin_access_nodes.end());
        std::map<Id,int>degree;for(auto e:routes[i].edges){++degree[e.a];++degree[e.b];}
        Id seed=0;double best=0;
        for(auto[v,n]:v34_usage(routes[i])){int x,y,l;decode(v,x,y,l);if(l!=1||mandatory.count(v)||degree[v]!=2)continue;auto&s=resource(v);double gain=scoring_cell(v,s.usage).phi-scoring_cell(v,s.usage-n).phi;if(gain>best||(gain==best&&gain>0&&v<seed)){seed=v;best=gain;}}
        if(!seed){++rejected["mandatory_or_branch_only"];continue;}
        ++attempted;high_tried+=d.nets[i].pins.size()>32;Json detail;
        bool ok=v4_try(i,seed,false,wire,limit,nullptr,0,0,0,detail);
        if(ok){++accepted;high_accepted+=d.nets[i].pins.size()>32;}else ++rejected[detail.at("reason").string()];
        if(progress&&attempted%32==0)progress->poll();
    }
    stats["candidate_nets"]=double(ranked.size());stats["high_pin_candidates"]=double(high);stats["attempted"]=double(attempted);stats["accepted"]=double(accepted);
    stats["high_pin_searched"]=double(high_tried);stats["high_pin_accepted"]=double(high_accepted);stats["before"]=before;stats["after"]=v4_attribution();stats["elapsed_seconds"]=elapsed()-began;
    stats["max_trials"]=2048;stats["phase_seconds"]=90;stats["single_net_node_limit"]=100000;stats["search_expansion_limit"]=double(v4_search_limit_);
    Json reasons=Json::object();for(auto[k,n]:rejected)reasons[k]=n;stats["rejection_reasons"]=reasons;v4_stats_["layer_escape"]=stats;
}

void Router::v4_tsv_refine(bool thermal_stage,double&wire,double limit,DensityCorrelation*density){
    Progress::Scope log(progress,thermal_stage?"V4_THERMAL_REFINEMENT":"V4_GLOBAL_TSV");double began=elapsed();
    Json stats=Json::object();std::map<Id,int> counts;size_t cross=0;for(auto&r:routes)if(r.cross){++counts[r.tsv];++cross;}
    const auto before=batch_quality_full();double rho0=density?density->value():0;
    auto multiplicity=[&](Id v){auto it=counts.find(v);return it==counts.end()?0:it->second;};
    // One deterministic priority queue per region. Reprice a bounded frontier
    // on each visit; accepted moves update both resource usage and sparse counts.
    using Entry=std::pair<double,size_t>;std::map<std::pair<int,int>,std::priority_queue<Entry,std::vector<Entry>,std::greater<Entry>>> regions;
    size_t high=0,eligible=0,large=0;std::vector<unsigned char> tried(routes.size(),0);
    for(size_t i=0;i<routes.size();i++)if(routes[i].cross){
        if(i%128==0)check_time();if(routes[i].nodes.size()>100000){++large;continue;}
        int x,y,l;decode(routes[i].tsv,x,y,l);double score=0;
        if(thermal_stage){if(!density)continue;score=-v3_thermal_rise_[v3_thermal_cell(routes[i].tsv)];}
        else for(auto[v,n]:v34_usage(routes[i])){auto&s=resource(v);score+=scoring_cell(v,s.usage).phi-scoring_cell(v,s.usage-n).phi;}
        regions[{x/32,y/32}].push({thermal_stage?-score:-score-std::log1p(multiplicity(routes[i].tsv)),i});++eligible;high+=d.nets[i].pins.size()>32;
    }
    const size_t max_trials=thermal_stage?12000:16000;const double phase_seconds=thermal_stage?120:180;
    double deadline=reroute_deadline_seconds_;reroute_deadline_seconds_=began+phase_seconds;
    struct Reset {double&value;double old;~Reset(){value=old;}}reset{reroute_deadline_seconds_,deadline};
    size_t attempts=0,moved=0,visited=0,no_site=0,high_tried=0,high_moved=0,joint=0,tradeoff=0;long long used_over=0;double used_phi=0;
    const long long over_budget=thermal_stage?static_cast<long long>(std::floor(before.overflow*.0001)):0;
    const double phi_budget=thermal_stage?before.phi*.0001:0;
    const size_t resource_limit=resources.size()+300000;std::map<std::string,int> reasons;std::string stop="regions_exhausted";
    bool remaining=true;while(remaining&&attempts<max_trials){remaining=false;
        for(auto&[region,queue]:regions){(void)region;
            if(queue.empty())continue;remaining=true;
            if(elapsed()>=reroute_deadline_seconds_){stop="phase_time_budget";remaining=false;break;}
            if(resources.size()>resource_limit){stop="materialized_resource_budget";remaining=false;break;}
            std::vector<Entry>frontier;
            for(int k=0;k<4&&!queue.empty();k++){
                size_t candidate=queue.top().second;queue.pop();if(tried[candidate])continue;double impact=0;
                if(thermal_stage)impact=-v3_thermal_rise_[v3_thermal_cell(routes[candidate].tsv)];
                else for(auto[v,n]:v34_usage(routes[candidate])){auto&s=resource(v);impact+=scoring_cell(v,s.usage).phi-scoring_cell(v,s.usage-n).phi;}
                frontier.push_back({thermal_stage?-impact:-impact-std::log1p(multiplicity(routes[candidate].tsv)),candidate});
            }
            if(frontier.empty())continue;std::sort(frontier.begin(),frontier.end());size_t i=frontier.front().second;
            for(size_t k=1;k<frontier.size();k++)queue.push(frontier[k]);
            tried[i]=1;++visited;high_tried+=d.nets[i].pins.size()>32;
            int x,y,l;decode(routes[i].tsv,x,y,l);std::set<Id> sites;
            for(int radius:{1,2,4,8,16,32})for(int dx:{-radius,0,radius})for(int dy:{-radius,0,radius})
                if((dx||dy)&&x+dx>=0&&x+dx<nx&&y+dy>=0&&y+dy<ny){Id v=id(x+dx,y+dy,10);if(tsv_legal(v))sites.insert(v);}
            std::vector<std::pair<double,Id>> candidates;
            for(Id site:sites){
                int sx,sy,sl;decode(site,sx,sy,sl);
                // Thermal is a fine adjustment after congestion repair. Large
                // hot-site jumps systematically fail the Via/wire guard; reserve
                // those radii for Stage A and spend Stage B on local Pareto moves.
                if(thermal_stage&&std::abs(sx-x)+std::abs(sy-y)>4)continue;
                double dr=density?density->after_move(v3_thermal_cell(routes[i].tsv),v3_thermal_cell(site))-(density->value()):0;
                if(thermal_stage&&dr<=1e-12)continue;
                Route footprint;footprint.cross=true;footprint.tsv=site;prepare_v34_route(d.nets[i],footprint);
                double price=0;for(auto[v,n]:v34_usage(footprint)){auto&s=resource(v);auto it=std::lower_bound(routes[i].resource_demand.begin(),routes[i].resource_demand.end(),v,[](auto a,Id b){return a.first<b;});int removed=it!=routes[i].resource_demand.end()&&it->first==v?it->second:0;auto a=scoring_cell(v,s.usage-removed),b=scoring_cell(v,s.usage-removed+n);price+=b.overflow-a.overflow+.1*(b.phi-a.phi);}
                // Ranking is a search proxy only. Acceptance is a vector of
                // full-route deltas, never a claimed official quality score.
                if(thermal_stage)price=-dr;else price+=.05*multiplicity(site);
                candidates.push_back({price,site});
            }
            std::sort(candidates.begin(),candidates.end());if(candidates.empty()){++no_site;continue;}
            // A fair first trial for every visited region, then at most one
            // alternative. No repeated full-Net search or quadratic TSV scan.
            if(candidates.size()>2)candidates.resize(2);
            for(auto[price,site]:candidates){(void)price;if(attempts>=max_trials)break;
                Id old_site=routes[i].tsv;auto inserted=counts.try_emplace(site,0).first;Json detail;++attempts;
                bool ok=v4_try(i,site,thermal_stage,wire,limit,density,thermal_stage?density->value()+1e-12:rho0-1e-5,over_budget-used_over,phi_budget-used_phi,detail);
                if(ok){++moved;high_moved+=d.nets[i].pins.size()>32;--counts.at(old_site);++inserted->second;
                    long long delta=static_cast<long long>(detail.at("overflow_delta").number());double phi=detail.at("potential_delta").number();used_over+=delta;used_phi+=phi;
                    joint+=delta<=0&&phi<=1e-9&&detail.at("rho_delta").number()>0;tradeoff+=delta>0||phi>1e-9;break;
                }++reasons[detail.at("reason").string()];
            }
            if(progress&&visited%64==0)progress->poll();
        }
        if(stop!="regions_exhausted")break;
    }
    if(attempts>=max_trials)stop="trial_budget";auto after=batch_quality_full();
    stats["eligible_nets"]=double(eligible);stats["all_tsv_nets"]=double(cross);stats["visited_nets"]=double(visited);stats["attempted"]=double(attempts);stats["moved"]=double(moved);
    stats["high_pin_candidates"]=double(high);stats["high_pin_visited"]=double(high_tried);stats["high_pin_moved"]=double(high_moved);stats["workspace_skipped"]=double(large);stats["no_ranked_legal_site"]=double(no_site);
    stats["joint_improving_moves"]=double(joint);stats["thermal_congestion_tradeoffs"]=double(tradeoff);stats["overflow_before"]=double(before.overflow);stats["overflow_after"]=double(after.overflow);
    stats["potential_before"]=before.phi;stats["potential_after"]=after.phi;stats["rho_before"]=rho0;stats["rho_after"]=density?density->value():0;
    stats["overflow_budget"]=double(over_budget);stats["potential_budget"]=phi_budget;stats["elapsed_seconds"]=elapsed()-began;stats["stop_reason"]=stop;
    stats["max_trials"]=double(max_trials);stats["phase_seconds"]=phase_seconds;stats["candidate_sites_per_net_limit"]=2;stats["search_expansion_limit"]=double(v4_search_limit_);
    Json rejects=Json::object();for(auto[k,n]:reasons)rejects[k]=n;stats["rejection_reasons"]=rejects;
    v4_stats_[thermal_stage?"thermal_refinement":"global_tsv"]=stats;
}

void Router::optimize_v4(){
    Progress::Scope log(progress,"V4_JOINT_OPTIMIZATION");v4_stats_["official_metrics"]=false;
    double wire=0;for(auto&r:routes){double cost=guide_grid_cost(r);if(std::isfinite(cost))wire+=cost;}
    const double original=wire,limit=wire*1.005;
    v4_stats_["m1_before"]=v4_attribution();
#ifndef GR_V4_DISABLE_ESCAPE
    v4_layer_escape(wire,limit);
#endif
    std::unique_ptr<DensityCorrelation>density;
    if(v3_thermal_ready_){std::vector<int>counts(v3_thermal_rise_.size());for(auto&r:routes)if(r.cross)++counts[v3_thermal_cell(r.tsv)];density=std::make_unique<DensityCorrelation>(v3_thermal_rise_,std::move(counts));}
#ifndef GR_V4_DISABLE_TSV
    v4_tsv_refine(false,wire,limit,density.get());
#endif
#ifndef GR_V4_DISABLE_THERMAL
    if(density)v4_tsv_refine(true,wire,limit,density.get());
#endif
    v4_stats_["m1_after"]=v4_attribution();v4_stats_["wire_delta_dbu"]=wire-original;v4_stats_["wire_growth_limit"]=.005;
    // Full Net validation remains mandatory, also for untouched high-Pin Nets.
    for(size_t i=0;i<routes.size();i++){if(i%128==0)check_time();validate(d.nets[i],routes[i]);}
}
}
