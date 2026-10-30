#pragma once
// Implementation is included once by router.cpp. Keeping the three production
// translation units preserves historical build/rebuild recipes and V1/V2 IDs.
namespace gr {
std::vector<Id> Router::search_multi(Id start,const std::unordered_set<Id>&targets,int lo,int hi,bool cross){
    if(targets.empty()||targets.size()>65536)throw V3Budget();
    TreeTarget tree;std::vector<Id> ordered(targets.begin(),targets.end());std::sort(ordered.begin(),ordered.end());
    std::vector<std::array<double,3>> points;points.reserve(ordered.size());
    for(Id v:ordered){add_target(tree,v);int x,y,l;decode(v,x,y,l);points.push_back({cfg.wire*x_centers_[x]/unit,cfg.wire*y_centers_[y]/unit,cfg.via*l});}
    tree.exact=std::make_shared<L1TargetIndex>(points);++v3_counts_["multi_target_searches"];
    v3_counts_["maximum_target_nodes"]=std::max(v3_counts_["maximum_target_nodes"],static_cast<long long>(targets.size()));
    return search_impl(start,0,lo,hi,cross,&tree);
}
Router::V3Topology Router::steiner_topology(const std::vector<Id>&terminals,int lo,int hi){
    auto solve=[&](const std::vector<Id>&points){
        V3Topology result;result.points=points;size_t n=points.size();std::vector<double> best(n,1e100);std::vector<int> parent(n,-1);std::vector<bool> seen(n,false);if(n)best[0]=0;
        for(size_t k=0;k<n;k++){
            int u=-1;for(size_t j=0;j<n;j++)if(!seen[j]&&(u<0||best[j]<best[u]))u=int(j);
            seen[u]=true;result.cost+=best[u];if(parent[u]>=0)result.edges.emplace_back(parent[u],u);
            auto[a,b]=center(points[u]);int x,y,l;decode(points[u],x,y,l);
            for(size_t v=0;v<n;v++)if(!seen[v]){auto[c,e]=center(points[v]);int xx,yy,ll;decode(points[v],xx,yy,ll);double value=cfg.wire*(std::abs(a-c)+std::abs(b-e))/unit+cfg.via*std::abs(l-ll);if(value<best[v]){best[v]=value;parent[v]=u;}}
        }return result;
    };
    V3Topology best=solve(terminals);
    if(terminals.size()<3)return best;
    if(terminals.size()>16){++v3_fallbacks_["topology_pin_limit"];return best;}
    std::set<int> xset,yset;std::map<int,int> layers;std::set<std::pair<int,int>> terminal_xy;
    for(Id v:terminals){int x,y,l;decode(v,x,y,l);xset.insert(x);yset.insert(y);++layers[l];terminal_xy.insert({x,y});}
    int primary=layers.begin()->first;for(auto[l,n]:layers)if(n>layers[primary])primary=l;
    std::vector<int> layer_choices{primary};
    for(int delta=1;delta<=9&&layer_choices.size()==1;delta++)for(int l:{primary-delta,primary+delta})if(l>=lo&&l<=hi&&d.layers.count(l)&&d.layers.at(l).direction!=d.layers.at(primary).direction){layer_choices.push_back(l);break;}
    std::vector<std::pair<double,Id>> candidates;
    for(int x:xset)for(int y:yset)if(!terminal_xy.count({x,y}))for(int l:layer_choices){
        Id v=id(x,y,l);double rank=0;auto[a,b]=center(v);
        for(Id pin:terminals){auto[c,e]=center(pin);rank+=std::abs(a-c)+std::abs(b-e);}
        candidates.emplace_back(rank,v);
    }
    std::sort(candidates.begin(),candidates.end());if(candidates.size()>96)candidates.resize(96);
    for(int round=0;round<2;round++){
        check_time();V3Topology winner=best;Id selected=0;
        for(const auto&[rank,v]:candidates){
            (void)rank;check_time();if(std::find(best.points.begin(),best.points.end(),v)!=best.points.end())continue;
            auto points=best.points;points.push_back(v);auto trial=solve(points);++v3_counts_["steiner_candidates"];
            int degree=0;for(auto[a,b]:trial.edges)degree+=(a==int(points.size()-1))+(b==int(points.size()-1));
            if(degree>=3&&trial.cost+1e-10<winner.cost){winner=std::move(trial);selected=v;}
        }
        if(!selected)break;
        winner.steiner=best.steiner;winner.steiner.push_back(selected);best=std::move(winner);
    }
    // Earlier inserted points can become leaves after another insertion.
    bool changed=true;while(changed){changed=false;std::vector<int> degree(best.points.size());for(auto[a,b]:best.edges){++degree[a];++degree[b];}
        for(size_t i=terminals.size();i<best.points.size();i++)if(degree[i]<3){auto points=best.points;points.erase(points.begin()+i);auto trial=solve(points);trial.steiner.assign(points.begin()+terminals.size(),points.end());best=std::move(trial);changed=true;break;}}
    v3_counts_["steiner_points_proposed"]+=static_cast<long long>(best.steiner.size());if(!best.steiner.empty())++v3_counts_["steiner_topologies_proposed"];
    else ++v3_fallbacks_["no_improving_hanan_point"];
    return best;
}
Route Router::route_steiner(const Net&n){
    Progress::Scope log(progress,"STEINER_ROUTE",true);++v3_counts_["steiner_route_attempts"];
    if(trace.out&&!trace.truncated){Json event=Json::object();event["net"]=n.name;event["pin_count"]=double(n.pins.size());event["strategy"]="bounded_hanan_steiner";trace.event("net_selected",event);}
    Route r;r.cross=n.cross;std::vector<Id> bottom,top;
    for(const auto&p:n.pins){Id v=pin_node(p);r.nodes.insert(v);(p.layer<=10?bottom:top).push_back(v);}
    if(n.cross){r.tsv=v3_forced_tsv_?v3_forced_tsv_:select_tsv(n);int x,y,l;decode(r.tsv,x,y,l);Id upper=id(x,y,11);bottom.push_back(r.tsv);top.push_back(upper);r.nodes.insert(r.tsv);r.nodes.insert(upper);r.edges.insert({r.tsv,upper});}
    auto connect=[&](std::vector<Id> pins,int lo,int hi){
        if(pins.empty())return;
        std::sort(pins.begin(),pins.end());pins.erase(std::unique(pins.begin(),pins.end()),pins.end());
        // Two terminals need no Steiner topology. Preserve fixed-target
        // orientation/ties instead of reversing a point-to-point search.
        if(pins.size()==2){
            Json event;if(trace.out&&!trace.truncated){event=Json::object();event["net"]=n.name;event["strategy"]="two_terminal_astar";event["from"]=double(pins[0]);event["to"]=double(pins[1]);trace.event("search_started",event);}
            auto path=search(pins[0],pins[1],lo,hi,n.cross);++v3_counts_["two_terminal_direct_searches"];
            if(trace.out&&!trace.truncated){event["nodes"]=numbers(path);trace.event("path_found",event);}
            for(Id v:path)r.nodes.insert(v);
            for(size_t i=1;i<path.size();i++)r.edges.insert({std::min(path[i-1],path[i]),std::max(path[i-1],path[i])});
            return;
        }
        auto topology=steiner_topology(pins,lo,hi);
        r.steiner_points.insert(r.steiner_points.end(),topology.steiner.begin(),topology.steiner.end());
        Id seed=topology.steiner.empty()?pins.front():topology.steiner.front();
        if(n.cross){int x,y,l;decode(r.tsv,x,y,l);seed=lo==1?r.tsv:id(x,y,11);}
        std::vector<std::vector<size_t>> adjacency(topology.points.size());for(auto[a,b]:topology.edges){adjacency[a].push_back(b);adjacency[b].push_back(a);}
        size_t root=size_t(std::find(topology.points.begin(),topology.points.end(),seed)-topology.points.begin());
        std::queue<size_t> queue;queue.push(root);std::vector<bool> seen(topology.points.size(),false);seen[root]=true;
        std::unordered_set<Id> connected{seed};r.nodes.insert(seed);
        if(trace.out&&!trace.truncated){Json event=Json::object();event["net"]=n.name;event["tier"]=lo==1?"bottom":"top";event["strategy"]="bounded_hanan_steiner";event["terminal_nodes"]=numbers(pins);event["steiner_nodes"]=numbers(topology.steiner);event["mst_edges"]=Json::array();for(auto[a,b]:topology.edges){Json e=Json::array();e.push(double(topology.points[a]));e.push(double(topology.points[b]));event["mst_edges"].push(e);}trace.event("topology",event);}
        while(!queue.empty()){
            check_time();size_t u=queue.front();queue.pop();Id terminal=topology.points[u];
            if(!connected.count(terminal)){
                if(trace.out&&!trace.truncated){Json event=Json::object();event["net"]=n.name;event["strategy"]="exact_l1_multi_target";event["from"]=double(terminal);event["target_count"]=double(connected.size());trace.event("search_started",event);}
                auto path=search_multi(terminal,connected,lo,hi,n.cross);
                if(trace.out&&!trace.truncated){Json event=Json::object();event["net"]=n.name;event["strategy"]="exact_l1_multi_target";event["from"]=double(terminal);event["to"]=double(path.back());event["nodes"]=numbers(path);trace.event("path_found",event);}
                for(Id v:path){r.nodes.insert(v);connected.insert(v);}
                for(size_t i=1;i<path.size();i++)r.edges.insert({std::min(path[i-1],path[i]),std::max(path[i-1],path[i])});
            }
            for(size_t v:adjacency[u])if(!seen[v]){seen[v]=true;queue.push(v);}
        }
    };
    connect(bottom,1,10);connect(top,11,20);make_guides(r);
    try{validate(n,r);}catch(const std::runtime_error&){++v3_fallbacks_["invalid_steiner_candidate"];throw V3Budget();}
    ++v3_counts_["steiner_routes_completed"];return r;
}
int Router::v3_fixed(Id v){
    if(!guide_model_ready_)return resource(v).fixed;
    auto found=v3_fixed_cache_.find(v);if(found!=v3_fixed_cache_.end())return found->second;
    int value=guide_fixed_demand(v);if(v3_fixed_cache_.size()<200000)v3_fixed_cache_.emplace(v,value);return value;
}
double Router::v3_penalty(Id v,int demand){
    const auto&r=resource(v);double cap=std::max(1,r.capacity),before=double(r.usage)+v3_fixed(v),after=before+demand;
    double a=std::max(0.0,before-r.capacity)/cap,b=std::max(0.0,after-r.capacity)/cap;
    double toll=0;if(v3_batch_active_){auto found=v3_batch_prices_.find(v);if(found!=v3_batch_prices_.end())toll=found->second;}
    return cfg.congestion*(v3_present_*(b*b-a*a)+0.05*std::pow(std::min(1.0,after/cap),4))+cfg.history*r.history+toll;
}
double Router::v3_marginal(const Route&r,int uncertainty){
    double value=0;int tx=0,ty=0,tl=0;if(r.cross)decode(r.tsv,tx,ty,tl);Id upper=r.cross?id(tx,ty,11):0;
    for(Id v:r.nodes){const auto&s=resource(v);int demand=1+int(r.cross&&(v==r.tsv||v==upper));double cap=std::max(1,s.capacity),base=double(s.usage)+v3_fixed(v)+uncertainty-s.capacity;double a=std::max(0.0,base)/cap,b=std::max(0.0,base+demand)/cap;value+=b*b-a*a;}
    return value;
}
double Router::guide_grid_cost(const Route&r)const{
    if(r.guides.size()>2048||r.nodes.size()>100000)return std::numeric_limits<double>::quiet_NaN();
    double total=0,pitch=std::max(xs[1]-xs[0],ys[1]-ys[0]);size_t visited=0;
    for(Id v:r.nodes){if(++visited%4096==0)check_time();int x,y,l;decode(v,x,y,l);total+=d.layers.at(l).direction=="HORIZONTAL"?xs[x+1]-xs[x]:ys[y+1]-ys[y];}
    std::array<std::vector<const Rect*>,21> layers;for(const auto&g:r.guides)layers[g.layer].push_back(&g);
    for(int l=1;l<20;l++)if(l!=10){check_time();for(const auto*a:layers[l])for(const auto*b:layers[l+1])if(std::min(a->x2,b->x2)>std::max(a->x1,b->x1)&&std::min(a->y2,b->y2)>std::max(a->y1,b->y1)){total+=3*pitch;break;}}
    return total+(r.cross?6*pitch:0);
}
void Router::negotiated_round(int iteration){
    Progress::Scope log(progress,"NEGOTIATED_CONGESTION");if(v3_cooldown_.size()!=routes.size())v3_cooldown_.assign(routes.size(),0);
    v3_present_=1+iteration;size_t scanned=0;
    for(auto&[v,res]:resources){int fixed=v3_fixed(v);res.history+=std::max(0.0,double(res.usage)+fixed-res.capacity)/std::max(1,res.capacity);if(++scanned%4096==0)check_time();}
    std::vector<std::pair<double,size_t>> order;
    for(size_t i=0;i<routes.size();i++){
        check_time();if(v3_cooldown_[i]>=iteration)continue;
        std::unordered_set<Id> mandatory;for(const auto&p:d.nets[i].pins)mandatory.insert(pin_node(p));
        if(routes[i].cross){int x,y,l;decode(routes[i].tsv,x,y,l);mandatory.insert(routes[i].tsv);mandatory.insert(id(x,y,11));}
        double contribution=0;
        for(Id v:routes[i].nodes)if(!mandatory.count(v)){const auto&s=resource(v);double over=std::max(0.0,double(s.usage)+v3_fixed(v)-s.capacity),cap=std::max(1,s.capacity);contribution+=(over*over-std::pow(std::max(0.0,over-1),2))/(cap*cap);}
        if(contribution>0)order.emplace_back(-contribution,i);
    }
    std::sort(order.begin(),order.end());
    struct Backup {size_t index;Route old;bool detached=false,replacement=false;};std::vector<Backup> saved;
    auto restore=[&](Backup&b){if(!b.detached)return;if(b.replacement)occupy(routes[b.index],-1);routes[b.index]=std::move(b.old);occupy(routes[b.index],1);b.detached=false;};
    double previous=reroute_deadline_seconds_;Id previous_tsv=v3_forced_tsv_;bool previous_phase=rerouting_;rerouting_=true;reroute_deadline_seconds_=elapsed()+20;
    struct Reset {double&deadline;double old;Id&tsv;Id old_tsv;bool&phase;bool old_phase;~Reset(){deadline=old;tsv=old_tsv;phase=old_phase;}} reset{reroute_deadline_seconds_,previous,v3_forced_tsv_,previous_tsv,rerouting_,previous_phase};
    int attempted=0,accepted=0,rejected=0,skipped=0;size_t backup_nodes=0;double wire_delta=0,phi_delta=0;std::string stop="candidates_exhausted";
    try{
        for(const auto&[key,index]:order){
            (void)key;if(attempted>=128){stop="candidate_budget";break;}if(elapsed()>=reroute_deadline_seconds_){stop="time_budget";break;}
            if(progress)progress->net(index+1,routes.size(),d.nets[index].name,iteration);
            double old_wire=guide_grid_cost(routes[index]);if(!std::isfinite(old_wire)){++skipped;continue;}
            if(backup_nodes+routes[index].nodes.size()>100000){stop="backup_node_budget";break;}
            saved.push_back({index,{}});auto&b=saved.back();b.old=std::move(routes[index]);routes[index]=Route{};occupy(b.old,-1);b.detached=true;
            double old_phi=v3_marginal(b.old),old_upper=v3_marginal(b.old,1);v3_forced_tsv_=b.old.cross?b.old.tsv:0;++attempted;
            bool exhausted=false,failed=false;
            try{routes[index]=route(d.nets[index]);}
            catch(const RerouteBudget&){exhausted=true;}
            catch(const RouterTimeout&){throw;}
            catch(const std::runtime_error&e){std::string text=e.what();if(text.find("No legal path")==0||text.find("A* expansion limit")==0)failed=true;else throw;}
            if(!exhausted&&!failed){
                double new_wire=guide_grid_cost(routes[index]),new_phi=v3_marginal(routes[index]),new_upper=v3_marginal(routes[index],1);
                if(std::isfinite(new_wire)&&new_wire<=old_wire+1e-6&&new_phi<=old_phi+1e-10&&new_upper<=old_upper+1e-10&&(new_wire<old_wire-1e-6||new_phi<old_phi-1e-10)){
                    occupy(routes[index],1);b.replacement=true;backup_nodes+=b.old.nodes.size();++accepted;wire_delta+=new_wire-old_wire;phi_delta+=new_phi-old_phi;
                    if(trace.out&&!trace.truncated){Json e=Json::object();e["net"]=d.nets[index].name;trace.event("net_removed",e);trace.event("net_added",route_json(d.nets[index],routes[index]));}
                    if(progress)progress->completed();
                    continue;
                }
            }
            restore(b);saved.pop_back();++rejected;v3_cooldown_[index]=iteration+1;if(progress)progress->completed();
            if(exhausted){stop="time_budget";break;}
        }
    }catch(const RerouteBudget&){
        if(!saved.empty()&&saved.back().detached&&!saved.back().replacement){restore(saved.back());saved.pop_back();++rejected;}
        stop="time_budget";
    }catch(...){for(auto it=saved.rbegin();it!=saved.rend();++it)restore(*it);throw;}
    try{
        Json round=Json::object();round["iteration"]=iteration;round["present_factor"]=v3_present_;round["attempted"]=attempted;round["accepted_nets"]=accepted;round["rejected_nets"]=rejected;round["skipped_nets"]=skipped;round["guide_wire_delta_dbu_estimate"]=wire_delta;round["normalized_overflow_potential_delta_estimate"]=phi_delta;round["stop_reason"]=stop;
        v3_rounds_.push_back(round);v3_counts_["negotiated_rounds"]++;v3_counts_["negotiated_attempts"]+=attempted;v3_counts_["negotiated_accepted"]+=accepted;v3_counts_["negotiated_rejected"]+=rejected;
        round["overflow_local"]=double(overflow());round["accepted"]=accepted>0;rounds.push_back(round);trace.event("negotiated_round",round);
    }catch(...){for(auto it=saved.rbegin();it!=saved.rend();++it)restore(*it);throw;}

}
void Router::build_v3_thermal(const Json&input){
    v3_thermal_ready_=false;v3_thermal_rise_.clear();
    auto axis=[&](char name,double lo,double hi){auto lines=d.grid.at(name);std::sort(lines.begin(),lines.end());lines.erase(std::unique(lines.begin(),lines.end()),lines.end());if(lines.front()>lo)lines.insert(lines.begin(),lo);if(lines.back()<hi)lines.push_back(hi);return lines;};
    v3_thermal_xs_=axis('X',d.die.x1,d.die.x2);v3_thermal_ys_=axis('Y',d.die.y1,d.die.y2);
    size_t width=v3_thermal_xs_.size()-1,height=v3_thermal_ys_.size()-1;
    if(!width||!height||width>1000000/height){++v3_fallbacks_["thermal_grid_memory_budget"];return;}
    if(input.at("nodes").arr.size()>2000000){++v3_fallbacks_["thermal_sample_memory_budget"];return;}
    std::map<std::pair<double,double>,std::pair<double,int>> projected;
    for(const auto&n:input.at("nodes").arr){double x=std::round(n.arr[0].number()*1e9)/1e9,y=std::round(n.arr[1].number()*1e9)/1e9;auto&a=projected[{x,y}];a.first+=n.arr[3].number();++a.second;}
    std::vector<double> values(width*height,0);std::vector<unsigned> count(values.size(),0);
    for(const auto&[xy,value]:projected){
        double x=d.die.x1+xy.first*d.dbu,y=d.die.y1+xy.second*d.dbu;
        if(x<v3_thermal_xs_.front()||x>v3_thermal_xs_.back()||y<v3_thermal_ys_.front()||y>v3_thermal_ys_.back())continue;
        size_t index=size_t(locate(v3_thermal_ys_,y))*width+locate(v3_thermal_xs_,x);values[index]+=value.first/value.second;++count[index];
    }
    bool known=false;for(size_t i=0;i<values.size();i++){if(count[i]){values[i]/=count[i];known=true;}else values[i]=std::numeric_limits<double>::quiet_NaN();}
    if(!known){++v3_fallbacks_["thermal_no_mapped_samples"];return;}
    // Fill within sampled rows first, then propagate complete rows vertically.
    // Preserve raw out-of-die grid cells: toy has 41x41, Router has 23x23.
    for(size_t y=0;y<height;y++){double previous=std::numeric_limits<double>::quiet_NaN();for(size_t x=0;x<width;x++){auto&v=values[y*width+x];if(std::isfinite(v))previous=v;else if(std::isfinite(previous))v=previous;}}
    for(size_t x=0;x<width;x++){double previous=std::numeric_limits<double>::quiet_NaN();for(size_t y=0;y<height;y++){auto&v=values[y*width+x];if(std::isfinite(v))previous=v;else if(std::isfinite(previous))v=previous;}}
    double next=ambient;for(size_t i=values.size();i-->0;){if(std::isfinite(values[i]))next=values[i];else values[i]=next;}
    for(auto&v:values)v=std::max(0.0,v-ambient);
    v3_thermal_rise_=std::move(values);v3_thermal_ready_=true;
}
size_t Router::v3_thermal_cell(Id v)const{
    auto[x,y]=center(v);return size_t(locate(v3_thermal_ys_,y))*(v3_thermal_xs_.size()-1)+locate(v3_thermal_xs_,x);
}
void Router::optimize_tsv_global(){
    Progress::Scope log(progress,"GLOBAL_TSV_OPTIMIZATION");
    if(!v3_thermal_ready_){++v3_fallbacks_["global_tsv_thermal_unavailable"];return;}
    ++v3_counts_["global_tsv_passes"];std::vector<int> counts(v3_thermal_rise_.size(),0);
    for(const auto&r:routes)if(r.cross)++counts[v3_thermal_cell(r.tsv)];
    DensityCorrelation density(v3_thermal_rise_,std::move(counts));double initial=density.value();
    auto candidates=[&](size_t index){
        const auto&r=routes[index];int x,y,l;decode(r.tsv,x,y,l);std::set<Id> sites;std::vector<int> px,py;
        for(const auto&p:d.nets[index].pins){int xx=locate(xs,p.x),yy=locate(ys,p.y);px.push_back(xx);py.push_back(yy);sites.insert(id(xx,yy,10));}
        std::sort(px.begin(),px.end());std::sort(py.begin(),py.end());if(!px.empty())sites.insert(id(px[px.size()/2],py[py.size()/2],10));
        for(int radius:{1,2,4})for(auto[dx,dy]:std::vector<std::pair<int,int>>{{-radius,0},{radius,0},{0,-radius},{0,radius}})if(x+dx>=0&&x+dx<nx&&y+dy>=0&&y+dy<ny)sites.insert(id(x+dx,y+dy,10));
        sites.erase(r.tsv);std::vector<std::pair<double,Id>> ranked;size_t old=v3_thermal_cell(r.tsv);
        for(Id site:sites){double gain=density.after_move(old,v3_thermal_cell(site))-density.value();if(gain>1e-12)ranked.emplace_back(-gain,site);}
        std::sort(ranked.begin(),ranked.end());if(ranked.size()>32)ranked.resize(32);return ranked;
    };
    std::vector<std::pair<double,size_t>> order;int fixed=0;
    for(size_t i=0;i<routes.size();i++)if(routes[i].cross){
        if(d.nets[i].pins.size()>64){++v3_fallbacks_["global_tsv_high_pin_limit"];continue;}
        check_time();std::set<Id> lower,upper;for(const auto&p:d.nets[i].pins){int x=locate(xs,p.x),y=locate(ys,p.y);if(p.layer==10)lower.insert(id(x,y,10));if(p.layer==11)upper.insert(id(x,y,10));}
        bool locked=false;for(Id v:lower)if(upper.count(v)){locked=true;break;}
        if(locked){++fixed;continue;}auto ranked=candidates(i);if(!ranked.empty())order.emplace_back(ranked.front().first,i);
    }
    std::sort(order.begin(),order.end());
    struct Backup {size_t index;Route old;bool detached=false,replacement=false;};std::vector<Backup> saved;
    auto restore=[&](Backup&b){if(!b.detached)return;if(b.replacement)occupy(routes[b.index],-1);routes[b.index]=std::move(b.old);occupy(routes[b.index],1);b.detached=false;};
    double previous=reroute_deadline_seconds_;Id previous_tsv=v3_forced_tsv_;bool previous_phase=rerouting_;rerouting_=true;reroute_deadline_seconds_=elapsed()+20;
    struct Reset {double&deadline;double old;Id&tsv;Id old_tsv;bool&phase;bool old_phase;~Reset(){deadline=old;tsv=old_tsv;phase=old_phase;}} reset{reroute_deadline_seconds_,previous,v3_forced_tsv_,previous_tsv,rerouting_,previous_phase};
    int attempts=0,moved=0,rejected=0;size_t backup_nodes=0;std::string stop="candidates_exhausted";
    try{
        for(const auto&[gain,index]:order){
            (void)gain;if(attempts>=32||moved>=16){stop="candidate_budget";break;}if(elapsed()>=reroute_deadline_seconds_){stop="time_budget";break;}
            auto ranked=candidates(index);size_t trials=0;
            for(const auto&[score,site]:ranked){
                (void)score;if(trials++>=2||attempts>=32)break;
                if(backup_nodes+routes[index].nodes.size()>100000){stop="backup_node_budget";break;}
                double old_wire=guide_grid_cost(routes[index]);if(!std::isfinite(old_wire))break;
                size_t from=v3_thermal_cell(routes[index].tsv),to=v3_thermal_cell(site);double next=density.after_move(from,to);
                if(next<=density.value()+1e-12)continue;
                saved.push_back({index,{}});auto&b=saved.back();b.old=std::move(routes[index]);routes[index]=Route{};occupy(b.old,-1);b.detached=true;
                double old_phi=v3_marginal(b.old),old_upper=v3_marginal(b.old,1);v3_forced_tsv_=site;++attempts;bool exhausted=false,failed=false;
                try{routes[index]=route(d.nets[index]);}
                catch(const RouterTimeout&){throw;}
                catch(const RerouteBudget&){exhausted=true;}
                catch(const std::runtime_error&e){std::string s=e.what();if(s.find("No legal path")==0||s.find("A* expansion limit")==0||s.find("Nonunique TSV")==0)failed=true;else throw;}
                if(!exhausted&&!failed){
                    double wire=guide_grid_cost(routes[index]);
                    if(routes[index].tsv==site&&std::isfinite(wire)&&wire<=old_wire+1e-6&&v3_marginal(routes[index])<=old_phi+1e-10&&v3_marginal(routes[index],1)<=old_upper+1e-10){
                        occupy(routes[index],1);b.replacement=true;
                        if(trace.out&&!trace.truncated){Json event=Json::object();event["net"]=d.nets[index].name;event["old_tsv"]=double(b.old.tsv);event["new_tsv"]=double(site);trace.event("net_removed",event);trace.event("net_added",route_json(d.nets[index],routes[index]));trace.event("global_tsv_moved",event);}
                        density.move(from,to);backup_nodes+=b.old.nodes.size();++moved;break;
                    }
                }
                restore(b);saved.pop_back();++rejected;
                if(exhausted){stop="time_budget";break;}
            }
            if(stop!="candidates_exhausted")break;
        }
    }catch(const RerouteBudget&){if(!saved.empty()&&saved.back().detached&&!saved.back().replacement){restore(saved.back());saved.pop_back();++rejected;}stop="time_budget";}
    catch(...){for(auto it=saved.rbegin();it!=saved.rend();++it)restore(*it);throw;}
    try{
        v3_global_tsv_stats_=Json::object();auto&s=v3_global_tsv_stats_;s["fixed_by_interface_pins"]=fixed;s["eligible_with_positive_marginal_correlation"]=double(order.size());s["attempted"]=attempts;s["moved"]=moved;s["rejected"]=rejected;s["correlation_before_estimate"]=initial;s["correlation_after_estimate"]=density.value();s["stop_reason"]=stop;s["official_metrics"]=false;
        v3_counts_["global_tsv_attempts"]+=attempts;v3_counts_["global_tsv_moved"]+=moved;trace.event("global_tsv_complete",s);
    }catch(...){for(auto it=saved.rbegin();it!=saved.rend();++it)restore(*it);throw;}
}
Json Router::v3_metadata()const{
    Json out=Json::object();out["generation"]=cfg.v3_batch?"v3_2_hotspot_multi_net":"steiner_negotiated_tsv_research";out["stage"]=cfg.v3_stage;
    if(cfg.v3_batch){Json batches=Json::array();for(const auto&row:v3_batch_rounds_)batches.push(row);out["hotspot_batches"]=batches;out["batch_quality_scope"]="whole materialized resource state; relative-wire proxy, normalized projected overflow, fixed TSV; not an official score";}
    auto count=[&](const char*key){auto it=v3_counts_.find(key);return it==v3_counts_.end()?0LL:it->second;};
    out["topology"]=count("steiner_topologies_proposed")?"bounded_hanan_steiner_with_terminal_mst_fallback":"terminal_topology_no_steiner_point";
    out["path_search"]=count("multi_target_searches")?"direct_astar_and_exact_l1_kdtree_multi_target":"direct_terminal_astar";
    out["congestion"]=count("hotspot_rounds")?"hotspot_multi_net_negotiated":count("negotiated_rounds")?"negotiated_projected_fixed_approximation":cfg.v3_stage>=2?"no_negotiated_round_executed":"legacy_congestion";
    out["tsv"]=count("global_tsv_passes")?"global_density_local_search":"local_tsv";out["official_metrics"]=false;
    Json counts=Json::object();for(auto[k,v]:v3_counts_)counts[k]=double(v);out["counters"]=counts;
    Json fallback=Json::object();for(auto[k,v]:v3_fallbacks_)fallback[k]=double(v);out["fallback_reasons"]=fallback;
    out["fallback_to_mst"]=v3_counts_.count("fallback_to_mst")?double(v3_counts_.at("fallback_to_mst")):0;
    out["fallback_to_legacy_congestion"]=cfg.v3_stage>=2&&!guide_model_ready_;
    out["fallback_to_local_tsv"]=cfg.v3_stage<3;
    if(cfg.v3_stage>=3){out["fallback_to_local_tsv"]=!v3_thermal_ready_||count("global_tsv_moved")==0;out["global_tsv"]=v3_global_tsv_stats_;}
    out["thermal_projection_ready"]=v3_thermal_ready_;out["thermal_grid_cells"]=double(v3_thermal_rise_.size());out["thermal_projection"]="full raw grid; horizontal then vertical fill; separate from legacy UI heat";
    out["fixed_model"]=guide_model_ready_?"projected_track_union_approximation":"legacy_fixed_approximation";out["fixed_model_uncertainty"]="acceptance checks projected demand and projected demand plus one; empirical guard, not a proven universal error bound";
    out["pricing_scope"]="stable initial cost; negotiated and global TSV trials use dynamic prices";
    out["price_cache_entries"]=double(v3_fixed_cache_.size());out["price_cache_limit"]=200000;out["steiner_terminal_limit"]=16;out["steiner_candidate_limit"]=96;out["target_node_limit"]=65536;
    double guide_cost=0;int unknown=0,steiner_routes=0;size_t steiner_nodes=0;for(const auto&r:routes){double value=guide_grid_cost(r);if(std::isfinite(value))guide_cost+=value;else ++unknown;steiner_routes+=!r.steiner_points.empty();steiner_nodes+=r.steiner_points.size();}
    out["final_routes_with_steiner_topology_nodes"]=steiner_routes;out["final_mapped_steiner_topology_nodes"]=double(steiner_nodes);
    out["guide_grid_wirelength_estimate"]=unknown?Json():Json(guide_cost);out["guide_grid_unknown_nets"]=unknown;
    out["guide_grid_cost_scope"]="generated nonoverlapping cell guides; via ratio 3, TSV ratio 6, nominal pitch; independently calibrated on public small cases";
    Json iterations=Json::array();for(const auto&r:v3_rounds_)iterations.push(r);out["rounds"]=iterations;return out;
}
}
