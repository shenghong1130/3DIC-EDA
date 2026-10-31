#pragma once
#include "physical_resources.hpp"
namespace gr {
void Router::build_v35_model(){
    batch_cache_ready_=false;v3_fixed_cache_.clear();guide_model_ready_=false;
    PhysicalModel model(d);scoring_tracks_=model.tracks;
    for(auto&[v,r]:resources){(void)v;r.fixed=0;r.scoring_ready=false;}
    model.build([&](Id v,int cap,int fixed){auto&r=resource(v);r.capacity=cap;r.fixed=fixed;r.scoring_ready=false;},[&](){check_time();});
    v34_tsv_blocked_=std::move(model.blocked);v34_pin_min_track_=model.min_pin_shapes;v34_fixed_cells_=model.fixed_cells;guide_interval_count_=model.intervals;
    v34_model_ready_=true;guide_model_ready_=true;for(auto&[v,r]:resources)r.capacity=scoring_capacity(v);
    v3_counts_["physical_stripe_workspace_bytes"]=model.max_workspace_bytes;v3_counts_["physical_spool_bytes"]=model.spool_bytes;
    std::map<std::pair<int,int>,std::pair<double,Id>> tiles;
    for(int y=0;y<ny;y++){if(y%64==0)check_time();for(int x=0;x<nx;x++){Id v=id(x,y,10);if(!tsv_legal(v))continue;double h=v3_thermal_ready_?v3_thermal_rise_[v3_thermal_cell(v)]:heat[size_t(y)*nx+x];auto key=std::make_pair(x/16,y/16);auto it=tiles.find(key);if(it==tiles.end()||h>it->second.first||(h==it->second.first&&v<it->second.second))tiles[key]={h,v};}}
    std::vector<std::pair<double,Id>> warm;for(auto&[tile,value]:tiles){(void)tile;warm.emplace_back(-value.first,value.second);}std::sort(warm.begin(),warm.end());for(size_t k=0;k<std::min<size_t>(64,warm.size());k++)v35_warm_sites_.push_back(warm[k].second);
    v3_counts_["legal_thermal_tile_candidates"]=tiles.size();trace.event("bounded_physical_model_built",rules_20261029_summary());
}
Route Router::route_v35_access(const Net&n){
    if(!v34_model_ready_)build_v34_model();Id prior_tsv=v3_forced_tsv_;bool prior_access=v35_access_active_;
    struct Reset {Router&r;Id tsv;bool active;~Reset(){r.v3_forced_tsv_=tsv;r.v35_access_active_=active;}} reset{*this,prior_tsv,prior_access};
    if(n.cross&&!v3_forced_tsv_)v3_forced_tsv_=select_tsv(n);
    Net selected=n;std::vector<Id> access;std::set<Id> low,high;double target_x=0,target_y=0;
    if(n.cross){std::tie(target_x,target_y)=center(v3_forced_tsv_);}else{for(auto&p:n.pins){target_x+=p.x;target_y+=p.y;}target_x/=n.pins.size();target_y/=n.pins.size();}
    for(size_t k=0;k<n.pins.size();k++){
        check_time();const auto&p=n.pins[k];double best=std::numeric_limits<double>::infinity();Id chosen=0;Pin point=p;size_t count=0;
        for(auto rect:pin_geometry(p))geometry_cells(rect,[&](Id v,const Rect&q){int x,y,l;decode(v,x,y,l);if((l<=10)!=(p.layer<=10))return;
            if(n.cross&&((l==10&&high.count(v%(Id(nx)*ny)))||(l==11&&low.count(v%(Id(nx)*ny)))))return;
            double px=(q.x1+q.x2)/2,py=(q.y1+q.y2)/2;
            double cost=(std::abs(px-target_x)+std::abs(py-target_y))+.01*(std::abs(px-p.x)+std::abs(py-p.y));++count;
            if(cost<best||(cost==best&&v<chosen)){best=cost;chosen=v;point={p.name,px,py,l};}
        });
        if(!std::isfinite(best))throw std::runtime_error("No legal Pin access assignment under nonoverlapping interface Guide model: "+n.name+" "+p.name);
        selected.pins[k]=point;access.push_back(chosen);if(point.layer==10)low.insert(chosen%(Id(nx)*ny));if(point.layer==11)high.insert(chosen%(Id(nx)*ny));
        if(chosen!=pin_node(p))++v3_counts_["geometry_access_changes_attempted"];if(count>1)++v3_counts_["multi_cell_pin_access_attempts"];
    }
    v35_access_active_=true;Route r=route(selected);r.pin_access_nodes=std::move(access);validate(n,r);return r;
}
Route Router::route_v35_local(const Net&n,const Route&old){
    // Reroute one degree-two interior path vertex, never the entire high-Pin
    // network. All terminals/TSV/branches stay fixed; existing group transaction
    // owns release/install/rollback and the final full connectivity validation.
    std::unordered_set<Id> mandatory(old.pin_access_nodes.begin(),old.pin_access_nodes.end());
    if(mandatory.empty())for(auto&p:n.pins)mandatory.insert(pin_node(p));if(old.cross){mandatory.insert(old.tsv);mandatory.insert(old.tsv+Id(nx)*ny);}
    std::map<Id,std::vector<Id>> adj;for(auto e:old.edges){adj[e.a].push_back(e.b);adj[e.b].push_back(e.a);}
    std::vector<std::pair<double,Id>> ranked;for(auto[v,count]:v34_usage(old)){if(mandatory.count(v)||!old.nodes.count(v)||adj[v].size()!=2)continue;const auto&r=resource(v);double before=std::max(0,r.fixed+r.usage-r.capacity),after=std::max(0,r.fixed+r.usage+count-r.capacity);if(after>before)ranked.emplace_back(-(after-before)/std::max(1,r.capacity),v);}
    std::sort(ranked.begin(),ranked.end());if(ranked.empty())throw std::runtime_error("No legal path local improvement candidate: "+n.name);
    Id v=ranked.front().second,a=adj.at(v)[0],b=adj.at(v)[1];Route candidate=old;candidate.nodes.erase(v);
    for(auto it=candidate.edges.begin();it!=candidate.edges.end();)if(it->a==v||it->b==v)it=candidate.edges.erase(it);else ++it;
    auto previous=std::move(v34_active_pin_cells_);v34_active_pin_cells_=own_pin_cells(n);double deadline=v3_route_deadline_;v3_route_deadline_=elapsed()+.15;
    struct Restore {Router&r;std::vector<Id>&pins;double deadline;~Restore(){r.v34_active_pin_cells_=std::move(pins);r.v3_route_deadline_=deadline;}} restore{*this,previous,deadline};
    alternative_avoid_.insert(v);int x,y,l;decode(a,x,y,l);++v3_counts_["high_pin_local_searches"];
    auto path=search(a,b,l<=10?1:11,l<=10?10:20,n.cross);for(Id node:path)candidate.nodes.insert(node);for(size_t k=1;k<path.size();k++)candidate.edges.insert({std::min(path[k-1],path[k]),std::max(path[k-1],path[k])});
    candidate.steiner_points.erase(std::remove_if(candidate.steiner_points.begin(),candidate.steiner_points.end(),[&](Id node){return !candidate.nodes.count(node);}),candidate.steiner_points.end());
    candidate.guides.clear();make_guides(candidate);prepare_v34_route(n,candidate);validate(n,candidate);return candidate;
}
}
