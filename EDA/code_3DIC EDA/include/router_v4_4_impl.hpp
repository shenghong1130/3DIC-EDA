#pragma once
#if !defined(GR_V4_3) || !defined(GR_V4_2) || !defined(GR_V3_6)
#error V4.4 requires the V4.3 and V4.2 legality/accounting code
#endif
namespace gr {
// Move TWO TSVs atomically. Single-Net V4.3 guards can reject a detour that
// becomes useful only when the other Net also releases a congested corridor.
// The resource oracle always uses complete old/new Route footprints.
bool Router::v44_try_tsv_pair(const std::array<size_t,2>&ids,
                             const std::array<Id,2>&targets,
                             double&total_wire,double wire_limit,
                             DensityCorrelation*density,Json&detail){
    detail=Json::object();detail["accepted"]=false;detail["reason"]="preflight";
    if(ids[0]==ids[1]||ids[0]>=routes.size()||ids[1]>=routes.size()||
       targets[0]==targets[1]){detail["reason"]="invalid_pair";return false;}
    double old_wire=0,old_ref=0;
    for(int k=0;k<2;k++){
        const Route&r=routes[ids[k]];
        if(!r.cross||r.nodes.size()>4096||d.nets[ids[k]].pins.size()>64||
           targets[k]==r.tsv||!tsv_legal(targets[k])){
            detail["reason"]="illegal_or_oversized_pair";return false;
        }
        int x,y,l;decode(targets[k],x,y,l);
        if(l!=10){detail["reason"]="target_not_m10";return false;}
        const double w=guide_grid_cost(r),ref=v43_reference_wire(r);
        if(!std::isfinite(w)||!std::isfinite(ref)){
            detail["reason"]="unmeasured_pair";return false;
        }
        old_wire+=w;old_ref+=ref;
    }
    // Preview the combined Pearson change in a copy. Mutate the real density
    // only after every potentially throwing validation and trace operation.
    double rho_delta=0;
    if(density){
        DensityCorrelation preview=*density;
        for(int k=0;k<2;k++)
            preview.move(v3_thermal_cell(routes[ids[k]].tsv),
                         v3_thermal_cell(targets[k]));
        rho_delta=preview.value()-density->value();
        if(rho_delta < -1e-5-1e-12){detail["reason"]="pair_thermal_guard";return false;}
    }
    bool prior_active=v4_search_active_,prior_rerouting=rerouting_;
    double prior_deadline=reroute_deadline_seconds_;
    auto prior_pins=std::move(v34_active_pin_cells_);
    struct Reset {
        Router&r;bool active,rerouting;double deadline;std::vector<Id>&pins;
        ~Reset(){r.v4_search_active_=active;r.rerouting_=rerouting;
                 r.reroute_deadline_seconds_=deadline;
                 r.v34_active_pin_cells_=std::move(pins);}
    }reset{*this,prior_active,prior_rerouting,prior_deadline,prior_pins};
    v4_search_active_=true;rerouting_=true;
    reroute_deadline_seconds_=prior_deadline>0?
        std::min(prior_deadline,elapsed()+.65):elapsed()+.65;
    V43BatchTransaction tx(*this,{ids[0],ids[1]});
    std::map<Id,std::pair<int,int>> footprint;
    double new_wire=0,new_ref=0;
    try{
        for(int k=0;k<2;k++){
            check_time();
            size_t i=ids[k];const Route&old=tx.states[k]->old;
            v34_active_pin_cells_=own_pin_cells(d.nets[i]);
            Route candidate;
#ifdef GR_V44_TEST
            if(v44_candidate_hook_)candidate=v44_candidate_hook_(i,old,targets[k]);
            else
#endif
            candidate=v4_local_tsv(i,old,targets[k]);
            candidate.guides.clear();make_guides(candidate);
            prepare_v34_route(d.nets[i],candidate);validate(d.nets[i],candidate);
            if(candidate.tsv!=targets[k]||candidate.pin_access_nodes!=old.pin_access_nodes){
                detail["reason"]="pin_or_tsv_changed";return false;
            }
            const double w=guide_grid_cost(candidate),ref=v43_reference_wire(candidate);
            if(!std::isfinite(w)||!std::isfinite(ref)||
               w>guide_grid_cost(old)*1.02+16*unit){
                detail["reason"]="per_net_wire_guard";return false;
            }
            auto vias=[&](const Route&t){
                int n=0;for(auto e:t.edges){
                    int x,y,l,xx,yy,ll;decode(e.a,x,y,l);decode(e.b,xx,yy,ll);
                    n+=l!=ll&&std::min(l,ll)!=10;
                }return n;
            };
            if(vias(candidate)>vias(old)+8){
                detail["reason"]="per_net_via_guard";return false;
            }
            for(auto[v,n]:v34_usage(old))footprint[v].first+=n;
            for(auto[v,n]:v34_usage(candidate))footprint[v].second+=n;
            new_wire+=w;new_ref+=ref;
            tx.states[k]->install(std::move(candidate));
#ifdef GR_V44_TEST
            if(v44_install_hook_)v44_install_hook_();
#endif
        }
        // Both new Routes are installed. Compare against the same occupancy
        // with both old Routes reinstated; all shared TSV tracks deduplicate
        // via the existing per-Route physical demand representation.
        long long over=0;double phi=0;
        for(auto[v,uses]:footprint){
            const Resource&s=resource(v);
            auto prior=scoring_cell(v,s.usage-uses.second+uses.first);
            auto now=scoring_cell(v,s.usage);
            over+=now.overflow-prior.overflow;
            phi+=now.phi-prior.phi;
        }
        const double dw=new_wire-old_wire,dr=new_ref-old_ref;
        detail["overflow_delta"]=double(over);
        detail["potential_delta"]=phi;
        detail["wire_delta_dbu"]=dw;
        detail["reference_wire_delta_dbu"]=dr;
        detail["rho_delta"]=rho_delta;
        if(total_wire+dw>wire_limit+1e-6||
           v43_wire_+dr>v43_wire_limit_+1e-6){
            detail["reason"]="global_wire_guard";return false;
        }
        if(over>0||phi>=-1e-9){
            detail["reason"]="no_joint_congestion_gain";return false;
        }
        detail["reason"]="joint_tsv_congestion_gain";
        detail["accepted"]=true;
        Json indices=Json::array(),sites=Json::array();
        for(int k=0;k<2;k++){indices.push(double(ids[k]));sites.push(double(targets[k]));}
        detail["net_indices"]=indices;detail["new_tsv_nodes"]=sites;
        trace.event("v44_tsv_pair_committed",detail);
        // move() has already been prevalidated using the same count snapshot.
        if(density)for(int k=0;k<2;k++)
            density->move(v3_thermal_cell(tx.states[k]->old.tsv),
                          v3_thermal_cell(targets[k]));
        total_wire+=dw;v43_wire_+=dr;tx.commit();
        return true;
    }catch(const RouterTimeout&){throw;}
    catch(const RerouteBudget&){detail["reason"]="pair_time_budget";return false;}
    catch(const V3Budget&){detail["reason"]="pair_local_search_budget";return false;}
    catch(const std::runtime_error&e){
        const std::string m=e.what();
        if(m.find("No legal path")==0||m.find("A* expansion")==0||
           m.find("Disconnected route")==0||m.find("Nonunique TSV")==0){
            detail["reason"]="infeasible_pair_patch";return false;
        }
        throw;
    }
}
// Regional global selection, not a per-Net greedy acceptance loop. Build a
// bounded priority list of cross-Die Net pairs, rank legal TSV alternatives by
// projected full TSV footprints, then test the complete two-Net delta.
void Router::v44_global_tsv_pairs(double&wire,double limit,DensityCorrelation*density){
    Progress::Scope phase(progress,"V44_GLOBAL_TSV_PAIRS");
    const double began=elapsed();
    Json stats=Json::object();stats["official_metrics"]=false;
    stats["ruleset"]="REFERENCE_20261103 proxy";
    constexpr double phase_seconds=12;
    constexpr size_t max_trials=24;
    // Leave headroom for the V4.3 thermal phase, Guide writing and scoring.
    double allowance=std::min(phase_seconds,cfg.time_limit-elapsed()-20.0);
    if(allowance<=1){stats["stop_reason"]="time_headroom";v44_stats_=stats;return;}
    const double prior_deadline=reroute_deadline_seconds_;
    reroute_deadline_seconds_=began+allowance;
    struct Restore {double&x;double old;~Restore(){x=old;}}
        restore{reroute_deadline_seconds_,prior_deadline};
    std::map<Id,int>multiplicity;
    std::map<std::pair<int,int>,std::vector<size_t>>regions;
    for(size_t i=0;i<routes.size();i++){
        if(i%256==0)check_time();
        const Route&r=routes[i];if(!r.cross)continue;
        ++multiplicity[r.tsv];
        if(r.nodes.size()>4096||d.nets[i].pins.size()>64)continue;
        int x,y,l;decode(r.tsv,x,y,l);
        regions[{x/16,y/16}].push_back(i);
    }
    struct Region {int sharing;size_t nets;std::pair<int,int>position;};
    std::vector<Region>order;
    for(auto&[region,ids]:regions)if(ids.size()>=2){
        int shared=0;for(size_t i:ids)shared=std::max(shared,multiplicity.at(routes[i].tsv));
        order.push_back({shared,ids.size(),region});
    }
    std::sort(order.begin(),order.end(),[](const Region&a,const Region&b){
        if(a.sharing!=b.sharing)return a.sharing>b.sharing;
        if(a.nets!=b.nets)return a.nets>b.nets;
        return a.position<b.position;
    });
    size_t attempts=0,accepted=0,visited=0,prechecked=0;
    std::map<std::string,int>rejects;std::string stop="regions_exhausted";
    auto options=[&](size_t i){
        int x,y,l;decode(routes[i].tsv,x,y,l);
        std::vector<std::pair<double,Id>>ranked;
        std::set<Id>seen;
        for(int r:{1,2,4})for(int dx:{-r,0,r})for(int dy:{-r,0,r}){
            if(!dx&&!dy)continue;
            int xx=x+dx,yy=y+dy;
            if(xx<0||xx>=nx||yy<0||yy>=ny)continue;
            Id site=id(xx,yy,10);
            if(!seen.insert(site).second||!tsv_legal(site)){++prechecked;continue;}
            Route footprint;footprint.cross=true;footprint.tsv=site;
            prepare_v34_route(d.nets[i],footprint);
            double cost=0;
            for(auto[v,n]:v34_usage(footprint)){
                auto&s=resource(v);
                if(s.scoring_capacity<=0){cost+=1e12;continue;}
                cost+=scoring_cell(v,s.usage+n).phi-scoring_cell(v,s.usage).phi;
            }
            int count=multiplicity.count(site)?multiplicity.at(site):0;
            cost+=0.01*(std::abs(dx)+std::abs(dy))+.01*count;
            ranked.push_back({cost,site});
        }
        std::sort(ranked.begin(),ranked.end());
        if(ranked.size()>3)ranked.resize(3);
        return ranked;
    };
    for(const auto&reg:order){
        if(attempts>=max_trials){stop="trial_budget";break;}
        if(elapsed()>=reroute_deadline_seconds_){stop="phase_time_budget";break;}
        auto&group=regions.at(reg.position);
        std::sort(group.begin(),group.end(),[&](size_t a,size_t b){
            const int ma=multiplicity.at(routes[a].tsv),mb=multiplicity.at(routes[b].tsv);
            if(ma!=mb)return ma>mb;
            if(routes[a].nodes.size()!=routes[b].nodes.size())
                return routes[a].nodes.size()<routes[b].nodes.size();
            return a<b;
        });
        // Two small, congested members of each area. Other regions still get
        // a chance; do not enumerate quadratic pairs on a 150k-Net case.
        std::array<size_t,2>ids{group[0],group[1]};
        auto left=options(ids[0]),right=options(ids[1]);++visited;
        struct Pair {double estimate;std::array<Id,2>sites;};
        std::vector<Pair>candidates;
        for(auto[a,sa]:left)for(auto[b,sb]:right)if(sa!=sb)
            candidates.push_back({a+b,{sa,sb}});
        std::sort(candidates.begin(),candidates.end(),[](const Pair&a,const Pair&b){
            if(a.estimate!=b.estimate)return a.estimate<b.estimate;
            return a.sites<b.sites;
        });
        size_t tries=0;
        for(auto&candidate:candidates){
            if(tries++>=2||attempts>=max_trials||
               elapsed()>=reroute_deadline_seconds_)break;
            ++attempts;Json detail;
            bool ok=v44_try_tsv_pair(ids,candidate.sites,wire,limit,density,detail);
            if(ok){
                ++accepted;
                for(int k=0;k<2;k++){
                    // Preserve old site counts from the successful transaction
                    // using the target and the other route's prior position.
                    // These priorities are only used for later regions.
                    ++multiplicity[candidate.sites[k]];
                }
                break;
            }
            ++rejects[detail.at("reason").string()];
        }
    }
    stats["attempted"]=double(attempts);
    stats["accepted"]=double(accepted);
    stats["visited_regions"]=double(visited);
    stats["legal_site_rejections"]=double(prechecked);
    stats["phase_seconds"]=allowance;
    stats["elapsed_seconds"]=elapsed()-began;
    stats["max_trials"]=double(max_trials);
    stats["stop_reason"]=stop;
    Json reasons=Json::object();
    for(auto&[key,value]:rejects)reasons[key]=double(value);
    stats["rejection_reasons"]=reasons;
    v44_stats_=stats;
}
}
