#pragma once
// V3.2 only. Each batch is a transaction over complete routes, not a sequence
// of individually accepted moves. Implementation is included once by router.cpp.
namespace gr {
Router::BatchQuality Router::batch_quality(){
    if(cfg.v3_calibrated&&batch_cache_ready_){++v3_counts_["quality_cache_hits"];return batch_cache_;}
    return batch_quality_full();
}
Router::BatchQuality Router::batch_quality_full(){
    BatchQuality out;size_t checked=0;
    for(const auto&[v,r]:resources){
        if(++checked%4096==0)check_time();
        if(cfg.v3_calibrated){auto q=scoring_cell(v,r.usage);out.phi+=q.phi;out.upper+=q.upper;out.overflow+=q.overflow;out.hotspots+=q.hotspots;continue;}
        double cap=std::max(1,r.capacity),fixed=v3_fixed(v);
        double over=std::max(0.0,r.usage+fixed-r.capacity),upper=std::max(0.0,r.usage+fixed+1-r.capacity);
        out.phi+=over*over/(cap*cap);out.upper+=upper*upper/(cap*cap);
        out.overflow+=static_cast<long long>(over);out.hotspots+=over>0;
    }
    return out;
}
bool Router::reroute_group(const std::vector<size_t>&group,int iteration,double&total_wire,double wire_reference,Json&detail){
    detail=Json::object();detail["accepted"]=false;detail["nets"]=numbers(group);
    if(group.size()<(cfg.v3_calibrated?1u:2u)||group.size()>8){detail["reason"]="group_size";return false;}
    std::set<size_t> unique(group.begin(),group.end());if(unique.size()!=group.size())throw std::runtime_error("Duplicate Net in hotspot transaction");
    struct Backup {size_t index;Route old;bool removed=false,installed=false;};
    std::vector<Backup> saved;saved.reserve(group.size());size_t nodes=0;double old_wire=0;
    std::unordered_map<Id,int> old_demand;
    for(size_t i:group){
        if(i>=routes.size())throw std::runtime_error("Invalid batch Net index");
        if(cfg.v3_calibrated&&!cfg.v3_geometry_bounded&&d.nets[i].pins.size()>32){detail["reason"]="high_pin_group_budget";return false;}
        double cost=guide_grid_cost(routes[i]);if(!std::isfinite(cost)){detail["reason"]="guide_budget";return false;}
        old_wire+=cost;nodes+=routes[i].nodes.size();if(nodes>20000){detail["reason"]="backup_node_budget";return false;}
        if(cfg.v3_rules_20261029){for(auto[v,n]:v34_usage(routes[i]))old_demand[v]+=n;}
        else{for(Id v:routes[i].nodes)++old_demand[v];
        if(routes[i].cross){int x,y,l;decode(routes[i].tsv,x,y,l);++old_demand[routes[i].tsv];++old_demand[id(x,y,11)];}}
    }
    // Preflight aggregate demand: shared cells must be released once per Net,
    // including both additional TSV units, before any mutation occurs.
    for(auto[v,n]:old_demand)if(resource(v).usage<n)throw std::runtime_error("Batch usage preflight failed");
    const auto before=batch_quality();double original_wire=total_wire;
    double prior_deadline=reroute_deadline_seconds_;Id prior_tsv=v3_forced_tsv_;bool prior_phase=rerouting_,prior_batch=v3_batch_active_;
    rerouting_=true;v3_batch_active_=true;v3_present_=1+iteration;
    double budget=cfg.v3_calibrated?.6:2;
    reroute_deadline_seconds_=prior_deadline>0?std::min(prior_deadline,elapsed()+budget):elapsed()+budget;
    struct Reset {Router&r;double deadline;Id tsv;bool phase,batch;~Reset(){r.reroute_deadline_seconds_=deadline;r.v3_forced_tsv_=tsv;r.rerouting_=phase;r.v3_batch_active_=batch;r.alternative_avoid_.clear();r.alternative_mandatory_.clear();}} reset{*this,prior_deadline,prior_tsv,prior_phase,prior_batch};
    auto restore=[&]{
        for(auto it=saved.rbegin();it!=saved.rend();++it){
            if(!it->removed)continue;
            if(it->installed)occupy(routes[it->index],-1);
            routes[it->index]=std::move(it->old);occupy(routes[it->index],1);it->removed=false;
        }
        total_wire=original_wire;
    };
    try{
        for(size_t i:group)saved.push_back({i,std::move(routes[i])});
        for(auto&b:saved){occupy(b.old,-1);b.removed=true;routes[b.index]=Route{};}
        double new_wire=0;int individually_longer=0;
        for(auto&b:saved){
            check_time();v3_forced_tsv_=b.old.cross?b.old.tsv:0;
            if(cfg.v3_calibrated){
                if(progress)progress->net(b.index+1,d.nets.size(),d.nets[b.index].name,iteration);
                alternative_mandatory_.clear();alternative_avoid_.clear();
                for(const auto&p:d.nets[b.index].pins)alternative_mandatory_.insert(pin_node(p));
                if(b.old.cross){int x,y,l;decode(b.old.tsv,x,y,l);alternative_mandatory_.insert(b.old.tsv);alternative_mandatory_.insert(id(x,y,11));}
                for(Id v:b.old.nodes)if(!alternative_mandatory_.count(v)&&scoring_cell(v,resource(v).usage+1).overflow)alternative_avoid_.insert(v);
            }
            Route candidate=cfg.v3_geometry_bounded&&d.nets[b.index].pins.size()>32?route_v35_local(d.nets[b.index],b.old):route(d.nets[b.index]);validate(d.nets[b.index],candidate);
            if(cfg.v3_calibrated){
                ++v3_counts_["alternative_routes_generated"];
                bool different=candidate.nodes!=b.old.nodes||candidate.edges.size()!=b.old.edges.size()||!std::equal(candidate.edges.begin(),candidate.edges.end(),b.old.edges.begin(),[](auto a,auto b){return a.a==b.a&&a.b==b.b;});
                if(different){++v3_counts_["distinct_route_candidates"];if(alternative_variant_==2)++v3_counts_["layer_escape_distinct_candidates"];}
            }
            if(candidate.cross&&candidate.tsv!=b.old.tsv)throw std::runtime_error("Batch changed fixed TSV anchor");
            double cost=guide_grid_cost(candidate);if(!std::isfinite(cost)){restore();detail["reason"]="guide_budget";return false;}
            individually_longer+=cost>guide_grid_cost(b.old)+1e-6;new_wire+=cost;
            // Complete all potentially allocating resource insertions BEFORE
            // installing demand. Rollback itself only touches existing entries.
            for(Id v:candidate.nodes)(void)resource(v);
            routes[b.index]=std::move(candidate);occupy(routes[b.index],1);b.installed=true;
            if(trace.out&&!trace.truncated){Json e=Json::object();e["net"]=d.nets[b.index].name;e["group_size"]=double(group.size());trace.event("hotspot_net_trial",e);}
        }
        check_time();auto after=batch_quality();double wire=original_wire+new_wire-old_wire;
        // Absolute wire scores depend on an external reference not available to
        // Router. Use a labelled relative-wire proxy; thermal delta is exactly
        // zero because this stage cannot move any TSV. Never report it as Q.
        auto congestion=[](double phi){return 100/std::sqrt(1+.01*phi);};
        double delta=.45*100*wire_reference*(1/wire-1/original_wire)+.30*(congestion(after.phi)-congestion(before.phi));
        bool accept=wire<=wire_reference*1.0025+1e-6&&after.phi<before.phi-1e-10&&after.upper<=before.upper+1e-10&&after.overflow<=before.overflow&&delta>1e-9;
        detail["wire_delta_dbu_estimate"]=wire-original_wire;detail["normalized_potential_delta"]=after.phi-before.phi;
        detail["overflow_delta_estimate"]=double(after.overflow-before.overflow);detail["hotspot_delta_estimate"]=double(after.hotspots)-double(before.hotspots);
        detail["relative_quality_proxy_delta"]=delta;detail["individually_longer_nets"]=individually_longer;detail["official_metrics"]=false;
        if(!accept){restore();detail["reason"]="joint_quality_guard";return false;}
        detail["reason"]="joint_improvement";detail["accepted"]=true;
        // Emit committed graph changes only after the COMPLETE group is legal.
        // A Trace failure is a hard error and restores the complete transaction.
        if(trace.out&&!trace.truncated){for(const auto&b:saved){Json e=Json::object();e["net"]=d.nets[b.index].name;trace.event("net_removed",e);trace.event("net_added",route_json(d.nets[b.index],routes[b.index]));}trace.event("hotspot_group_committed",detail);}
        total_wire=wire;return true;
    }catch(const RouterTimeout&){restore();throw;}
    catch(const RerouteBudget&){restore();detail["reason"]="group_or_round_budget";return false;}
    catch(const V3Budget&){restore();detail["reason"]="search_budget";return false;}
    catch(const std::runtime_error&e){
        restore();std::string message=e.what();
        if(message.find("No legal path")==0||message.find("A* expansion limit")==0||message.find("Nonunique TSV")==0||(cfg.v3_geometry_bounded&&message.find("No legal Pin access")==0)){detail["reason"]="unroutable_group";return false;}
        throw;
    }catch(...){restore();throw;}
}
void Router::hotspot_round(int iteration){
    if(cfg.v3_calibrated){calibrated_hotspot_round(iteration);return;}
    Progress::Scope log(progress,"HOTSPOT_MULTI_NET");Json report=Json::object();report["iteration"]=iteration;report["accepted_groups"]=0;
    auto before=batch_quality();double total_wire=0;
    for(const auto&r:routes){double cost=guide_grid_cost(r);if(!std::isfinite(cost)){report["stop_reason"]="global_guide_estimate_unknown";v3_batch_rounds_.push_back(report);return;}total_wire+=cost;}
    double wire_reference=total_wire;
    std::vector<std::pair<double,Id>> ranked;ranked.reserve(2048);
    std::priority_queue<std::pair<double,Id>> hottest;
    size_t scanned=0;
    for(auto&[v,r]:resources){
        int fixed=v3_fixed(v);double cap=std::max(1,r.capacity),a=std::max(0.0,double(r.usage)+fixed-r.capacity),b=std::max(0.0,double(fixed)-r.capacity);
        r.history+=a/cap;if(r.usage&&a>b){std::pair<double,Id> entry{-(a*a-b*b)/(cap*cap),v};if(hottest.size()<2048)hottest.push(entry);else if(entry<hottest.top()){hottest.pop();hottest.push(entry);}}
        if(++scanned%4096==0)check_time();
    }
    // Bound the inverse index to 2048 hot resources x 16 owners. We scan route
    // nodes once instead of retaining a full design-wide node->Net index.
    while(!hottest.empty()){ranked.push_back(hottest.top());hottest.pop();}std::sort(ranked.begin(),ranked.end());
    std::unordered_map<Id,std::vector<size_t>> owners;for(auto[key,v]:ranked){(void)key;owners.emplace(v,std::vector<size_t>{});}
    size_t visits=0;int mandatory_hits=0,truncated=0;
    for(size_t i=0;i<routes.size();i++){
        std::unordered_set<Id> mandatory;for(const auto&p:d.nets[i].pins)mandatory.insert(pin_node(p));
        if(routes[i].cross){int x,y,l;decode(routes[i].tsv,x,y,l);mandatory.insert(routes[i].tsv);mandatory.insert(id(x,y,11));}
        for(Id v:routes[i].nodes){
            if(++visits>2000000){report["stop_reason"]="inverse_index_scan_budget";v3_batch_rounds_.push_back(report);return;}
            auto found=owners.find(v);if(found!=owners.end()){if(mandatory.count(v))++mandatory_hits;else if(found->second.size()<16)found->second.push_back(i);else ++truncated;}
        }
        if(i%64==0)check_time();
    }
    double previous=reroute_deadline_seconds_;reroute_deadline_seconds_=elapsed()+20;
    struct Deadline {double&value;double previous;~Deadline(){value=previous;}} deadline{reroute_deadline_seconds_,previous};
    v3_batch_prices_.clear();double pitch=cfg.wire*std::max(xs[1]-xs[0],ys[1]-ys[0])/unit;
    for(auto[key,v]:ranked){(void)key;auto&r=resource(v);double over=std::max(0.0,double(r.usage)+v3_fixed(v)-r.capacity);v3_batch_prices_[v]=pitch*(1+iteration)*std::min(4.0,over/std::max(1,r.capacity));}
    std::set<std::vector<size_t>> seen;std::vector<int> attempts(routes.size());Json trials=Json::array();int groups=0,accepted=0,rejected=0,nets=0,longer=0;std::string stop="hotspots_exhausted";
    try{
        for(auto[key,hot]:ranked){
            (void)key;if(groups>=64){stop="group_budget";break;}check_time();
            std::vector<size_t> group;for(size_t i:owners.at(hot))if(attempts[i]<2&&routes[i].nodes.count(hot))group.push_back(i);
            if(group.size()<2)continue;
            std::sort(group.begin(),group.end(),[&](size_t a,size_t b){return routes[a].nodes.size()!=routes[b].nodes.size()?routes[a].nodes.size()>routes[b].nodes.size():a<b;});if(group.size()>8)group.resize(8);
            auto signature=group;std::sort(signature.begin(),signature.end());if(!seen.insert(signature).second)continue;
            ++groups;for(size_t i:group)++attempts[i];bool committed=false;
            // Two bounded orderings, always restored before the second trial.
            // This is not a second Router run or a new version experiment.
            for(int order=0;order<2&&!committed;order++){
                Json detail;committed=reroute_group(group,iteration,total_wire,wire_reference,detail);detail["order"]=order;detail["hotspot_node"]=double(hot);trials.push(detail);
                if(committed){++accepted;nets+=int(group.size());longer+=int(detail.at("individually_longer_nets").number());}
                else std::reverse(group.begin(),group.end());
                check_time();
            }
            if(!committed)++rejected;
        }
    }catch(const RerouteBudget&){stop="round_time_budget";rejected=groups-accepted;}
    // Remove the soft deadline before observational summaries; global timeout
    // still applies. Every completed group is already legal and committed.
    reroute_deadline_seconds_=previous;auto after=batch_quality();
    report["attempted_groups"]=groups;report["accepted_groups"]=accepted;report["rejected_groups"]=rejected;report["accepted_net_moves"]=nets;
    report["accepted_individually_longer_nets"]=longer;report["indexed_hotspots"]=double(ranked.size());report["mandatory_pin_tsv_hits"]=mandatory_hits;report["owner_entries_truncated"]=truncated;
    report["normalized_potential_before"]=before.phi;report["normalized_potential_after"]=after.phi;report["overflow_before_estimate"]=double(before.overflow);report["overflow_after_estimate"]=double(after.overflow);
    report["hotspots_before_estimate"]=double(before.hotspots);report["hotspots_after_estimate"]=double(after.hotspots);report["wire_delta_dbu_estimate"]=total_wire-wire_reference;
    report["stop_reason"]=stop;report["official_metrics"]=false;report["trials"]=trials;v3_batch_rounds_.push_back(report);
    ++v3_counts_["hotspot_rounds"];v3_counts_["hotspot_groups_attempted"]+=groups;v3_counts_["hotspot_groups_accepted"]+=accepted;v3_counts_["hotspot_groups_rejected"]+=rejected;v3_counts_["hotspot_accepted_net_moves"]+=nets;v3_counts_["hotspot_individually_longer_accepted"]+=longer;
    Json summary=report;summary.obj.erase("trials");rounds.push_back(summary);trace.event("hotspot_round",summary);
}
}
