#pragma once
// V3.3: hash-pinned scorer compatibility, bounded alternatives, dirty-cell sums.
// Physical Pin/OBS parsing and the legacy Resource.fixed field stay unchanged.
namespace gr {
Router::BatchQuality Router::scoring_cell(Id v,int usage){
    auto&r=resource(v);
    if(!r.scoring_ready){r.scoring_fixed=cfg.v3_calibrated?scoring_fixed_demand(v):v3_fixed(v);r.scoring_capacity=cfg.v3_calibrated?scoring_capacity(v):r.capacity;r.scoring_ready=true;}
    double demand=double(usage)+r.scoring_fixed,cap=std::max(1,r.scoring_capacity);
    double over=std::max(0.0,demand-r.scoring_capacity),upper=std::max(0.0,demand+1-r.scoring_capacity);
    BatchQuality q;q.overflow=static_cast<long long>(over);q.hotspots=over>0;
    q.phi=r.scoring_capacity<=0?(demand>0?1000000:0):over*over/(cap*cap);
    q.upper=r.scoring_capacity<=0?1000000:upper*upper/(cap*cap);return q;
}
void Router::change_usage(Id v,int sign){
    auto&r=resource(v);if(r.usage+sign<0)throw std::runtime_error("Negative resource usage");
    if(batch_cache_ready_){
        auto a=scoring_cell(v,r.usage),b=scoring_cell(v,r.usage+sign);
        batch_cache_.phi+=b.phi-a.phi;batch_cache_.upper+=b.upper-a.upper;
        batch_cache_.overflow+=b.overflow-a.overflow;
        if(b.hotspots>a.hotspots)++batch_cache_.hotspots;else if(b.hotspots<a.hotspots)--batch_cache_.hotspots;
    }
    r.usage+=sign;
}
void Router::calibrated_hotspot_round(int iteration){
    Progress::Scope log(progress,"CALIBRATED_HOTSPOT_BATCH");
    // Full scan once per round. Search-created resources register their fixed
    // baseline once in resource(); all occupancy changes use exact dirty deltas.
    batch_cache_ready_=false;batch_cache_=batch_quality_full();batch_cache_ready_=true;
    struct Reset {Router&r;double deadline;~Reset(){r.batch_cache_ready_=false;r.alternative_variant_=0;r.alternative_avoid_.clear();r.alternative_mandatory_.clear();r.v3_batch_prices_.clear();r.reroute_deadline_seconds_=deadline;}} reset{*this,reroute_deadline_seconds_};
    auto before=batch_cache_;Json report=Json::object();report["iteration"]=iteration;report["accepted_groups"]=0;report["official_metrics"]=false;
    double total_wire=0;for(const auto&r:routes){double c=guide_grid_cost(r);if(!std::isfinite(c)){report["stop_reason"]="guide_budget";v3_batch_rounds_.push_back(report);return;}total_wire+=c;}
    double reference=total_wire;
    std::vector<std::pair<double,Id>> ranked;size_t scanned=0;
    for(auto&[v,r]:resources){
        if(++scanned%4096==0)check_time();
        if(!r.usage)continue;
        auto q=scoring_cell(v,r.usage),fixed=scoring_cell(v,0);
        if(q.phi>fixed.phi){ranked.emplace_back(-(q.phi-fixed.phi),v);r.history+=std::max(0.0,double(r.usage+r.scoring_fixed-r.scoring_capacity))/std::max(1,r.scoring_capacity);}
    }
    std::sort(ranked.begin(),ranked.end());if(ranked.size()>4096)ranked.resize(4096);
    std::unordered_map<Id,std::vector<size_t>> owners;for(auto[key,v]:ranked){(void)key;owners.emplace(v,std::vector<size_t>{});}
    size_t visits=0;bool bounded=false;int mandatory_hits=0;
    for(size_t i=0;i<routes.size()&&!bounded;i++){
        if(i%64==0)check_time();
        if(d.nets[i].pins.size()>32)continue;
        std::unordered_set<Id> mandatory;for(const auto&p:d.nets[i].pins)mandatory.insert(pin_node(p));
        if(routes[i].cross){int x,y,l;decode(routes[i].tsv,x,y,l);mandatory.insert(routes[i].tsv);mandatory.insert(id(x,y,11));}
        for(Id v:routes[i].nodes){
            if(++visits>2000000){bounded=true;break;}
            auto it=owners.find(v);if(it==owners.end())continue;
            if(mandatory.count(v)){++mandatory_hits;continue;}
            if(it->second.size()<16)it->second.push_back(i);
        }
    }
    // Partial owner index is usable: it never invents an owner or removes a
    // mandatory Pin/TSV. State this budget explicitly for large designs.
    reroute_deadline_seconds_=elapsed()+8;
    for(auto[key,v]:ranked){(void)key;auto&r=resource(v);v3_batch_prices_[v]=cfg.wire*(1+iteration)*std::min(8.0,double(r.usage+r.scoring_fixed)/std::max(1,r.scoring_capacity));}
    std::set<std::vector<size_t>> seen;std::vector<int> attempts(routes.size());Json trials=Json::array();int groups=0,accepted=0,moved=0;std::string stop="hotspots_exhausted";
    try{
        for(auto[key,hot]:ranked){
            (void)key;check_time();if(groups>=48){stop="group_budget";break;}
            std::vector<size_t> group;for(size_t i:owners.at(hot))if(attempts[i]<2&&routes[i].nodes.count(hot))group.push_back(i);
            if(group.empty())continue;
            std::sort(group.begin(),group.end(),[&](size_t a,size_t b){return routes[a].nodes.size()!=routes[b].nodes.size()?routes[a].nodes.size()<routes[b].nodes.size():a<b;});if(group.size()>4)group.resize(4);
            auto signature=group;std::sort(signature.begin(),signature.end());if(!seen.insert(signature).second)continue;
            ++groups;for(size_t i:group)++attempts[i];bool committed=false;
            for(int variant:{2,1,0}){
                alternative_variant_=variant;Json detail;committed=reroute_group(group,iteration,total_wire,reference,detail);
                detail["variant"]=variant;detail["hotspot_node"]=double(hot);trials.push(detail);
                if(committed){++accepted;moved+=int(group.size());break;}
                check_time();
            }
        }
    }catch(const RerouteBudget&){stop="round_time_budget";}
    reroute_deadline_seconds_=reset.deadline;
    auto after=batch_cache_;
    // An independent production sanity scan only ONCE, never per candidate.
    auto oracle=batch_quality_full();
    if(std::abs(after.phi-oracle.phi)>1e-7*std::max(1.0,oracle.phi)||after.overflow!=oracle.overflow||after.hotspots!=oracle.hotspots)throw std::runtime_error("Dirty-cell quality diverged from full oracle");
    report["attempted_groups"]=groups;report["accepted_groups"]=accepted;report["accepted_net_moves"]=moved;
    report["indexed_hotspots"]=double(ranked.size());report["owner_scan_truncated"]=bounded;report["mandatory_pin_tsv_hits"]=mandatory_hits;
    report["normalized_potential_before"]=before.phi;report["normalized_potential_after"]=after.phi;report["overflow_before_estimate"]=double(before.overflow);report["overflow_after_estimate"]=double(after.overflow);
    report["hotspots_before_estimate"]=double(before.hotspots);report["hotspots_after_estimate"]=double(after.hotspots);report["wire_delta_dbu_estimate"]=total_wire-reference;
    report["stop_reason"]=stop;report["trials"]=trials;v3_batch_rounds_.push_back(report);
    ++v3_counts_["hotspot_rounds"];v3_counts_["hotspot_groups_attempted"]+=groups;v3_counts_["hotspot_groups_accepted"]+=accepted;v3_counts_["hotspot_groups_rejected"]+=groups-accepted;v3_counts_["hotspot_accepted_net_moves"]+=moved;
    report.obj.erase("trials");rounds.push_back(report);trace.event("hotspot_round",report);
}
}
