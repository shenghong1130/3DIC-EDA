#pragma once
#include "sparse_tsv_assignment.hpp"
namespace gr {
void Router::plan_initial_tsv(){
    Progress::Scope phase(progress,"EXPERIMENTAL_INITIAL_TSV_ASSIGNMENT");
    initial_tsv_plan_.clear();initial_tsv_stats_["status"]="BUILDING";
    initial_tsv_stats_["scope"]="planned TSV footprints only; access-wire and thermal costs are proxies; NOT an official score";
    double began=elapsed();size_t edges=0;
    struct PlanningBudget{};
    auto poll=[&](){check_time();if(elapsed()-began>45)throw PlanningBudget{};if(progress)progress->poll();};
    try{
        tsv_assignment::Problem problem;std::vector<size_t> indices;
        for(size_t i=0;i<d.nets.size();i++){
            poll();const auto&n=d.nets[i];if(!n.cross)continue;
            Id base=select_tsv(n);int x,y,l;decode(base,x,y,l);
            std::set<Id> sites{base};
            for(int radius:{1,2,4,8})for(int dx:{-radius,0,radius})for(int dy:{-radius,0,radius})
                if((dx||dy)&&x+dx>=0&&x+dx<nx&&y+dy>=0&&y+dy<ny)sites.insert(id(x+dx,y+dy,10));
            std::vector<std::pair<double,Id>> rank;
            for(Id site:sites){poll();if(!tsv_legal(site))continue;auto[cx,cy]=center(site);double wire=0;
                // Both dies contribute every Pin; same units/normalization as
                // baseline select_tsv. It is an access estimate, not routed WL.
                for(const auto&p:n.pins)wire+=(std::abs(p.x-cx)+std::abs(p.y-cy))/unit;
                wire*=cfg.wire/std::max(size_t(1),n.pins.size());
                int sx,sy,sl;decode(site,sx,sy,sl);
                double cold=heat_max_>heat_min_?(heat_max_-heat[sy*nx+sx])/(heat_max_-heat_min_):0;
                rank.emplace_back(wire+cfg.thermal*std::max(0.,cold),site);
            }
            std::sort(rank.begin(),rank.end());
            if(rank.size()>24){auto it=std::find_if(rank.begin(),rank.end(),[&](auto v){return v.second==base;});auto baseline=*it;rank.resize(24);if(std::none_of(rank.begin(),rank.end(),[&](auto v){return v.second==base;}))rank.back()=baseline;}
            std::vector<tsv_assignment::Choice> choices;
            for(auto[cost,site]:rank){
                Route footprint;footprint.cross=true;footprint.tsv=site;prepare_v34_route(n,footprint);
                tsv_assignment::Choice choice;choice.site=site;choice.access_cost=cost;bool valid=true;
                for(auto[v,count]:v34_usage(footprint)){
                    if(!count)continue;scoring_cell(v,0);const auto&s=resource(v);
                    if(s.scoring_capacity<=0){valid=false;break;}
                    choice.demand.emplace_back(v,count);problem.resources.emplace(v,tsv_assignment::Resource{s.scoring_capacity,s.scoring_fixed});
                }
                if(valid&&!choice.demand.empty()){edges+=choice.demand.size();if(edges>4000000)throw PlanningBudget{};choices.push_back(std::move(choice));}
            }
            // No candidate: let the unchanged Router explain infeasibility.
            if(!choices.empty()){indices.push_back(i);problem.nets.push_back(std::move(choices));}
        }
        auto solution=tsv_assignment::solve(problem,poll);
        // Publish only a fully constructed plan. No occupancy is changed here.
        std::map<size_t,Id> plan;
        for(size_t j=0;j<indices.size();j++)plan.emplace(indices[j],Id(problem.nets[j][solution.choice[j]].site));
        initial_tsv_plan_.swap(plan);
        initial_tsv_stats_["status"]="PLANNED";initial_tsv_stats_["nets"]=double(indices.size());
        initial_tsv_stats_["predicted_footprint_phi"]=solution.phi;initial_tsv_stats_["predicted_shared_sites"]=double(solution.shared_sites);
        initial_tsv_stats_["coordinate_moves"]=double(solution.moves);initial_tsv_stats_["pair_swaps"]=double(solution.swaps);
    }catch(const PlanningBudget&){initial_tsv_stats_["status"]="BUDGET_FALLBACK_UNCHANGED_INITIAL_ROUTER";}
    initial_tsv_stats_["footprint_entries"]=double(edges);initial_tsv_stats_["seconds"]=elapsed()-began;
    initial_tsv_stats_["route_fallbacks"]=0;initial_tsv_stats_["planned_routes_completed"]=0;
}
Route Router::route_initial_exp(size_t i){
    auto it=initial_tsv_plan_.find(i);if(it==initial_tsv_plan_.end())return route(d.nets[i]);
    Id old=v3_forced_tsv_;struct Reset{Id&v;Id old;~Reset(){v=old;}} reset{v3_forced_tsv_,old};
    v3_forced_tsv_=it->second;
    try{
        auto candidate=route(d.nets[i]);validate(d.nets[i],candidate);
        initial_tsv_stats_["planned_routes_completed"]=initial_tsv_stats_.at("planned_routes_completed").number()+1;return candidate;
    }catch(const RouterTimeout&){throw;}
    catch(const std::bad_alloc&){throw;}
    catch(const std::runtime_error&e){
        std::string message=e.what();
        if(message.find("No legal path")!=0&&message.find("A* expansion")!=0&&message.find("Disconnected route")!=0&&message.find("Nonunique TSV")!=0)throw;
        // route() does not occupy resources. Only the caller commits a complete
        // validated Net, so this fallback cannot double-charge the TSV.
        v3_forced_tsv_=old;
        initial_tsv_stats_["route_fallbacks"]=initial_tsv_stats_.at("route_fallbacks").number()+1;
        return route(d.nets[i]);
    }
}
}
