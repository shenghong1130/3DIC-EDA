#pragma once
// V3.6 ONLY. Keep the V3.5 graph, Pin access, geometry and search unchanged.
#if !defined(GR_V3_5) || !defined(GR_V3_FINAL)
#error GR_V3_6 requires GR_V3_5 and GR_V3_FINAL
#endif
namespace gr {
// One-Net undo state. No full graph copy, no history of accepted route copies.
// occupy() preflights every allocation/underflow before changing usage; restore
// touches only previously materialized resources and the moved old Route.
struct Router::V36Transaction {
    Router&r;size_t index;Route old;bool detached=false,installed=false,committed=false;
    V36Transaction(Router&router,size_t i):r(router),index(i){
        old=std::move(r.routes.at(i));
        try{r.occupy(old,-1);detached=true;}catch(...){r.routes[i]=std::move(old);throw;}
        r.routes[i]=Route{};
    }
    void install(Route candidate){
        // Candidate insertion may allocate during the preflight, never midway
        // through a usage update. The old route is still owned by this guard.
        r.routes[index]=std::move(candidate);r.occupy(r.routes[index],1);installed=true;
    }
    void rollback(){
        if(!detached||committed)return;
        if(installed)r.occupy(r.routes[index],-1);
        r.routes[index]=std::move(old);r.occupy(r.routes[index],1);detached=false;
    }
    ~V36Transaction(){rollback();}
};

Router::BatchQuality Router::v36_insertion(const Route&route){
    BatchQuality delta;
    for(auto[v,n]:v34_usage(route)){
        auto&s=resource(v);auto a=scoring_cell(v,s.usage),b=scoring_cell(v,s.usage+n);
        delta.overflow+=b.overflow-a.overflow;delta.phi+=b.phi-a.phi;
    }
    return delta;
}

bool Router::v36_try_tsv(size_t index,Id site,double&total_wire,double wire_limit,
                        DensityCorrelation*density,double rho_floor,Json&detail){
    detail=Json::object();detail["accepted"]=false;
    if(index>=routes.size()||!routes[index].cross||site==routes[index].tsv||!tsv_legal(site)){
        detail["reason"]="illegal_or_unchanged_site";return false;
    }
    const double old_wire=guide_grid_cost(routes[index]);
    if(!std::isfinite(old_wire)){detail["reason"]="wire_workspace_budget";return false;}
    const Id old_site=routes[index].tsv;
    size_t from=0,to=0;double next_rho=0;
    if(density){from=v3_thermal_cell(old_site);to=v3_thermal_cell(site);next_rho=density->after_move(from,to);
        if(next_rho+1e-12<rho_floor){detail["reason"]="thermal_guard";return false;}}
    double prior_deadline=reroute_deadline_seconds_;Id prior_tsv=v3_forced_tsv_;bool prior_phase=rerouting_;
    rerouting_=true;v3_forced_tsv_=site;
    reroute_deadline_seconds_=prior_deadline>0?std::min(prior_deadline,elapsed()+.12):elapsed()+.12;
    struct Reset {Router&r;double deadline;Id tsv;bool phase;~Reset(){r.reroute_deadline_seconds_=deadline;r.v3_forced_tsv_=tsv;r.rerouting_=phase;}}
        reset{*this,prior_deadline,prior_tsv,prior_phase};
    V36Transaction transaction(*this,index);
    const auto old_delta=v36_insertion(transaction.old);
    Route candidate;double wire=0;BatchQuality next_delta;
    try{candidate=route(d.nets[index]);validate(d.nets[index],candidate);
        wire=guide_grid_cost(candidate);next_delta=v36_insertion(candidate);}
    catch(const RerouteBudget&){detail["reason"]="candidate_time_budget";return false;}
    catch(const V3Budget&){detail["reason"]="candidate_search_budget";return false;}
    catch(const RouterTimeout&){throw;}
    catch(const std::runtime_error&e){
        const std::string s=e.what();
        if(s.find("No legal path")==0||s.find("A* expansion limit")==0||s.find("Nonunique TSV")==0||s.find("No legal Pin access")==0){detail["reason"]="infeasible_path";return false;}
        throw;
    }
    const double pitch=std::max(xs[1]-xs[0],ys[1]-ys[0]);
    const auto over=next_delta.overflow-old_delta.overflow;
    const double phi=next_delta.phi-old_delta.phi;
    detail["overflow_delta"]=double(over);detail["potential_delta"]=phi;detail["wire_delta_dbu_estimate"]=wire-old_wire;
    // Compare COMPLETE old/new demand against the SAME removed-Net baseline.
    // Fewer TSVs in one cell is only a priority signal, never an accept rule.
    if(candidate.tsv!=site||!std::isfinite(wire)||wire>old_wire*1.05+8*pitch||total_wire+wire-old_wire>wire_limit+1e-6){
        detail["reason"]="wire_guard";return false;
    }
    if(over>0||phi>1e-9||!(over<0||phi< -1e-8)){detail["reason"]="no_joint_congestion_improvement";return false;}
    transaction.install(std::move(candidate));
#ifdef GR_V36_TEST
    if(v36_abort_after_install_)throw std::runtime_error("Injected V3.6 interruption after install");
#endif
    // Potentially allocating detail writes precede commit, so an exception
    // still restores routes, TSV footprint demand and every changed layer.
    detail["accepted"]=true;detail["reason"]="joint_congestion_improvement";
    detail["old_tsv"]=double(old_site);detail["new_tsv"]=double(site);
    if(density)density->move(from,to);
    total_wire+=wire-old_wire;transaction.committed=true;
    return true;
}

void Router::redistribute_tsv_global(){
    Progress::Scope log(progress,"GLOBAL_TSV_REDISTRIBUTION");
    const double began=elapsed();constexpr size_t max_nets=2048,max_trials=8192,max_per_net=4;
    constexpr double phase_seconds=120,wire_fraction=.0025,rho_loss=.00001;
    std::map<Id,int> multiplicity;
    size_t tsvs=0;for(const auto&r:routes)if(r.cross){++multiplicity[r.tsv];++tsvs;}
    auto summary=[&](){std::array<size_t,3>s{};for(auto[v,n]:multiplicity){(void)v;if(n>0)++s[0];if(n>1)++s[1];s[2]=std::max(s[2],size_t(n));}return s;};
    auto before_density=summary();
    std::unique_ptr<DensityCorrelation> thermal;
    if(v3_thermal_ready_){std::vector<int>bins(v3_thermal_rise_.size(),0);for(const auto&r:routes)if(r.cross)++bins[v3_thermal_cell(r.tsv)];thermal=std::make_unique<DensityCorrelation>(v3_thermal_rise_,std::move(bins));}
    const double initial_rho=thermal?thermal->value():0;
    double wire_reference=0;size_t unknown_wire=0;
    for(const auto&r:routes){check_time();double w=guide_grid_cost(r);if(std::isfinite(w))wire_reference+=w;else ++unknown_wire;}
    // Uncostable nets are never moved; omitting their nonnegative cost makes
    // the global growth budget conservative rather than disabling all moves.
    double total_wire=wire_reference;const double wire_limit=wire_reference*(1+wire_fraction);
    const auto before=batch_quality_full();
    struct Ranked {int colocated;double contribution;size_t index;};std::vector<Ranked>order;order.reserve(tsvs);
    size_t skipped=0;
    for(size_t i=0;i<routes.size();i++)if(routes[i].cross){
        if(i%128==0)check_time();const auto&r=routes[i];
        if(d.nets[i].pins.size()>32||r.nodes.size()>4096||!std::isfinite(guide_grid_cost(r))){++skipped;continue;}
        double impact=0;for(auto[v,n]:v34_usage(r)){auto&s=resource(v);impact+=scoring_cell(v,s.usage).phi-scoring_cell(v,s.usage-n).phi;}
        if(multiplicity[r.tsv]>1||impact>1e-8)order.push_back({multiplicity[r.tsv],impact,i});
    }
    std::sort(order.begin(),order.end(),[](const Ranked&a,const Ranked&b){
        if(a.colocated!=b.colocated)return a.colocated>b.colocated;
        if(a.contribution!=b.contribution)return a.contribution>b.contribution;return a.index<b.index;
    });
    if(order.size()>max_nets)order.resize(max_nets);
    const double prior_deadline=reroute_deadline_seconds_;reroute_deadline_seconds_=began+phase_seconds;
    struct Reset {double&value;double old;~Reset(){value=old;}} reset{reroute_deadline_seconds_,prior_deadline};
    size_t attempts=0,moved=0,rejected=0,candidates_scored=0;
    std::map<std::string,long long>reasons;std::string stop="bounded_candidates_exhausted";
    const double pitch=std::max(xs[1]-xs[0],ys[1]-ys[0]);
    for(const auto&item:order){
        if(attempts>=max_trials){stop="trial_budget";break;}
        if(elapsed()>=reroute_deadline_seconds_){stop="phase_time_budget";break;}
        const size_t index=item.index;int x,y,l;decode(routes[index].tsv,x,y,l);std::set<Id>sites;
        for(int radius:{1,2,4,8})for(int dx:{-radius,0,radius})for(int dy:{-radius,0,radius})
            if((dx||dy)&&x+dx>=0&&x+dx<nx&&y+dy>=0&&y+dy<ny)sites.insert(id(x+dx,y+dy,10));
        auto count=[&](Id v){auto it=multiplicity.find(v);return it==multiplicity.end()?0:it->second;};
        std::vector<std::pair<double,Id>>ranked;
        const auto&old=routes[index];
        for(Id site:sites){
            if(!tsv_legal(site))continue;
            Route foot;foot.cross=true;foot.tsv=site;prepare_v34_route(d.nets[index],foot);
            double over=0,phi=0;
            for(auto[v,n]:v34_usage(foot)){
                auto&s=resource(v);auto it=std::lower_bound(old.resource_demand.begin(),old.resource_demand.end(),v,[](auto a,Id b){return a.first<b;});
                int removed=it!=old.resource_demand.end()&&it->first==v?it->second:0;
                auto a=scoring_cell(v,s.usage-removed),b=scoring_cell(v,s.usage-removed+n);over+=b.overflow-a.overflow;phi+=b.phi-a.phi;
            }
            auto[cx,cy]=center(site);double distance=0;for(const auto&p:d.nets[index].pins)distance+=std::abs(p.x-cx)+std::abs(p.y-cy);
            distance/=std::max(size_t(1),d.nets[index].pins.size())*pitch;
            double heat_delta=thermal?thermal->after_move(v3_thermal_cell(old.tsv),v3_thermal_cell(site))-thermal->value():0;
            ranked.emplace_back(over+.2*phi+.025*distance+.05*count(site)-10*heat_delta,site);++candidates_scored;
        }
        std::sort(ranked.begin(),ranked.end());if(ranked.size()>max_per_net)ranked.resize(max_per_net);
        for(auto[price,site]:ranked){
            (void)price;if(attempts>=max_trials||elapsed()>=reroute_deadline_seconds_)break;
            auto inserted=multiplicity.try_emplace(site,0).first;const Id old_site=routes[index].tsv;
            Json detail;++attempts;
            bool accepted=false;
            try{accepted=v36_try_tsv(index,site,total_wire,wire_limit,thermal.get(),initial_rho-rho_loss,detail);}
            catch(const RerouteBudget&){stop="phase_time_budget";break;}
            if(accepted){
                --multiplicity.at(old_site);++inserted->second;++moved;
                if(trace.out&&!trace.truncated){detail["net"]=d.nets[index].name;trace.event("net_removed",detail);trace.event("net_added",route_json(d.nets[index],routes[index]));trace.event("global_tsv_redistributed",detail);}
                break;
            }
            ++rejected;++reasons[detail.at("reason").string()];
        }
        if(stop=="phase_time_budget")break;
        if(progress&&attempts%32==0)progress->poll();
    }
    reroute_deadline_seconds_=prior_deadline;
    const auto after=batch_quality_full();auto after_density=summary();
    auto&s=v3_global_tsv_stats_;s=Json::object();s["strategy"]="global_congestion_aware_tsv_redistribution";
    s["official_metrics"]=false;s["attempted"]=double(attempts);s["moved"]=double(moved);s["rejected"]=double(rejected);
    s["candidate_footprints_scored"]=double(candidates_scored);s["eligible_ranked_nets"]=double(order.size());s["skipped_large_or_uncostable_nets"]=double(skipped);
    s["tsv_count"]=double(tsvs);s["colocated_gcells_before"]=double(before_density[1]);s["colocated_gcells_after"]=double(after_density[1]);
    s["unique_tsv_gcells_before"]=double(before_density[0]);s["unique_tsv_gcells_after"]=double(after_density[0]);
    s["excess_colocated_tsvs_before"]=double(tsvs-before_density[0]);s["excess_colocated_tsvs_after"]=double(tsvs-after_density[0]);
    s["max_tsv_per_gcell_before"]=double(before_density[2]);s["max_tsv_per_gcell_after"]=double(after_density[2]);
    s["overflow_before_estimate"]=double(before.overflow);s["overflow_after_estimate"]=double(after.overflow);
    s["potential_before_estimate"]=before.phi;s["potential_after_estimate"]=after.phi;s["wire_delta_dbu_estimate"]=total_wire-wire_reference;
    s["correlation_before_estimate"]=thermal?Json(initial_rho):Json();s["correlation_after_estimate"]=thermal?Json(thermal->value()):Json();
    s["wire_budget_fraction"]=wire_fraction;s["thermal_max_correlation_loss"]=rho_loss;s["uncostable_unmoved_nets"]=double(unknown_wire);
    s["max_net_budget"]=double(max_nets);s["max_trials"]=double(max_trials);s["max_trials_per_net"]=double(max_per_net);s["phase_budget_seconds"]=phase_seconds;
    s["elapsed_seconds"]=elapsed()-began;s["stop_reason"]=stop;
    Json rejects=Json::object();for(auto[k,n]:reasons)rejects[k]=double(n);s["rejection_reasons"]=rejects;
    ++v3_counts_["global_tsv_passes"];v3_counts_["global_tsv_attempts"]+=attempts;v3_counts_["global_tsv_moved"]+=moved;
    trace.event("global_tsv_redistribution_complete",s);
}
}
