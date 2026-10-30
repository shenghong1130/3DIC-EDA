#include "router.hpp"
#include <algorithm>
#include <queue>
#include <unordered_set>
#include <numeric>
#include <fstream>
#include <iostream>
namespace gr {
#ifdef GR_PROFILE
namespace {
struct ProfileTimer {
    double& result;std::chrono::steady_clock::time_point start=std::chrono::steady_clock::now();
    explicit ProfileTimer(double&r):result(r){}
    ~ProfileTimer(){result+=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();}
};
}
#define GR_COUNT(field,value) (profile.field+=(value))
#define GR_MAX(field,value) (profile.field=std::max<std::uint64_t>(profile.field,(value)))
#define GR_TIMER(field) ProfileTimer timer_##field(profile.field)
Router::~Router() noexcept {
    try{const char*path=std::getenv("GR_PROFILE_PATH");if(!path||!*path)return;Json j=Json::object();
#define GR_FIELD(name) j[#name]=double(profile.name)
        GR_FIELD(astar_calls);GR_FIELD(expansions);GR_FIELD(max_expansions);GR_FIELD(queue_pops);GR_FIELD(stale_pops);GR_FIELD(max_dist_nodes);
        GR_FIELD(sparse_attempts);GR_FIELD(dense_attempts);GR_FIELD(max_workspace_bytes);GR_FIELD(margin_attempts);
        GR_FIELD(capacity_calls);GR_FIELD(capacity_cache_hits);GR_FIELD(resource_insertions);GR_FIELD(tsv_calls);GR_FIELD(tsv_candidates);GR_FIELD(heat_values_scanned);
        GR_FIELD(rerouted_nets);GR_FIELD(route_copy_count);GR_FIELD(route_move_count);GR_FIELD(rollback_nets);GR_FIELD(route_json_calls);
        GR_FIELD(tree_searches);GR_FIELD(tree_target_nodes_max);GR_FIELD(tree_terminal_skips);
        GR_FIELD(astar_seconds);GR_FIELD(tsv_seconds);GR_FIELD(heat_scan_seconds);GR_FIELD(backup_seconds);GR_FIELD(rollback_seconds);GR_FIELD(reroute_seconds);
#undef GR_FIELD
        std::ofstream out(std::filesystem::u8path(path));j.write(out);out<<'\n';
    }catch(...){}
}
#else
#define GR_COUNT(field,value) ((void)0)
#define GR_MAX(field,value) ((void)0)
#define GR_TIMER(field) ((void)0)
#endif
Router::Router(Design&design,Config c,Trace&t,Progress*p):d(design),cfg(c),trace(t),started(std::chrono::steady_clock::now()),progress(p){
#ifdef GR_V3_FINAL
    cfg.v3_final=true;
#ifdef GR_V3_STAGE
    cfg.v3_stage=GR_V3_STAGE;
#else
    cfg.v3_stage=3;
#endif
#endif
#ifdef GR_V3_2
    cfg.v3_batch=true;
#endif
    if(cfg.v3_final){
        cfg.net_order_mode=0;cfg.tree_route_mode=0;cfg.reroute_priority_mode=0;
        cfg.congestion_cost_mode=0;cfg.congestion_scope_mode=0;cfg.reroute_policy_mode=0;
        cfg.reroute_max_nets=512;cfg.reroute_pass_seconds=20;cfg.reroute_wire_growth=0;
    }
    if(c.net_order_mode<0||c.net_order_mode>2||c.tree_route_mode<0||c.tree_route_mode>1||c.reroute_priority_mode<0||c.reroute_priority_mode>1)throw std::runtime_error("Invalid V3 strategy mode");
    if(c.congestion_cost_mode<0||c.congestion_cost_mode>2||c.reroute_policy_mode<0||c.reroute_policy_mode>1||c.reroute_max_nets<1||!std::isfinite(c.reroute_pass_seconds)||c.reroute_pass_seconds<=0||!std::isfinite(c.reroute_wire_growth)||c.reroute_wire_growth<0||c.reroute_wire_growth>0.1)throw std::runtime_error("Invalid congestion strategy configuration");
    if(c.congestion_scope_mode<0||c.congestion_scope_mode>1)throw std::runtime_error("Invalid congestion scope mode");
    auto lines=[&](char a,double lo,double hi){std::vector<double> v{lo,hi};for(double x:d.grid.at(a))if(x>lo&&x<hi)v.push_back(x);std::sort(v.begin(),v.end());v.erase(std::unique(v.begin(),v.end()),v.end());return v;};
    xs=lines('X',d.die.x1,d.die.x2);ys=lines('Y',d.die.y1,d.die.y2);nx=int(xs.size())-1;ny=int(ys.size())-1;
    if(nx<1||ny<1||Id(nx)*ny>10000000)throw std::runtime_error("Unsupported GCell size (>10M cells)");
    x_centers_.reserve(nx);y_centers_.reserve(ny);
    for(int x=0;x<nx;x++)x_centers_.push_back((xs[x]+xs[x+1])/2);
    for(int y=0;y<ny;y++)y_centers_.push_back((ys[y]+ys[y+1])/2);
    refresh_layers();
    unit=std::min(xs[1]-xs[0],ys[1]-ys[0]);heat.assign(size_t(nx)*ny,ambient);thermal_count.assign(heat.size(),0);routes.resize(d.nets.size());
    Json j=Json::object();j["cols"]=nx;j["rows"]=ny;j["layer_count"]=int(d.layers.size());trace.event("graph_built",j);
}
int Router::locate(const std::vector<double>&a,double x)const{if(x<a.front()||x>a.back())throw std::runtime_error("Coordinate outside grid");return std::min(int(a.size())-2,int(std::upper_bound(a.begin(),a.end(),x)-a.begin())-1);}
Id Router::pin_node(const Pin&p)const{return id(locate(xs,p.x),locate(ys,p.y),p.layer);}
Rect Router::cell(Id v)const{int x,y,l;decode(v,x,y,l);return {xs.at(x),ys.at(y),xs.at(x+1),ys.at(y+1),l};}
std::pair<double,double> Router::center(Id v)const{int x,y,l;decode(v,x,y,l);return {x_centers_.at(x),y_centers_.at(y)};}
void Router::refresh_layers(){
    for(int l=1;l<=20;l++){auto it=d.layers.find(l);layer_present_[l]=it!=d.layers.end();layer_horizontal_[l]=layer_present_[l]&&it->second.direction=="HORIZONTAL";via_up_[l]=d.vias.count({l,l+1});via_down_[l]=d.vias.count({l-1,l});}
}
void Router::refresh_heat_stats(){
    GR_TIMER(heat_scan_seconds);GR_COUNT(heat_values_scanned,2*heat.size());
    heat_min_=*std::min_element(heat.begin(),heat.end());heat_max_=*std::max_element(heat.begin(),heat.end());
}
double Router::elapsed()const{return std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();}
void Router::check_time()const{if(progress)progress->poll();double seconds=elapsed();if(seconds>cfg.time_limit)throw RouterTimeout();if(reroute_deadline_seconds_>0&&seconds>=reroute_deadline_seconds_)throw RerouteBudget();if(v3_route_deadline_>0&&seconds>=v3_route_deadline_)throw V3Budget();}
double Router::congestion_penalty(const Resource&r,int demand)const{
    double cap=std::max(1,r.capacity),before=double(r.usage)+r.fixed;
    int mode=cfg.congestion_scope_mode==1&&!rerouting_?0:cfg.congestion_cost_mode;
    // Mode 0 is bit-for-bit the original one-unit proxy, including TSV access.
    double overload=std::max(0.0,before+1-r.capacity)/cap;
    if(mode==0)return cfg.congestion*(overload*overload)+cfg.history*r.history;
    double after=before+demand,increment=std::max(0.0,after-r.capacity)-std::max(0.0,before-r.capacity);
    if(mode==1)return cfg.congestion*increment+cfg.history*r.history;
    double utilization=std::min(1.0,std::max(0.0,after/cap));
    overload=std::max(0.0,after-r.capacity)/cap;
    return cfg.congestion*(0.25*increment+0.05*std::pow(utilization,4)+overload*overload)+cfg.history*r.history;
}
static int count_tracks(const Track&t,double lo,double hi,bool last){double e=1e-8;int begin=std::max(0,int(std::ceil((lo-t.start)/t.step-e)));int end=std::min(t.count,int(std::ceil((hi-t.start)/t.step-(last?-e:e))));return std::max(0,end-begin);}
void Router::refresh_capacities(){
    // Capacity depends on one axis per layer, not on every 3D grid node.
    // Bound optional tables to 8 MiB; use the original calculation otherwise.
    size_t remaining=8*1024*1024;
    for(int l=1;l<=20;l++){
        std::vector<int>().swap(axis_capacity_[l]);if(!layer_present_[l])continue;
        const auto&lines=layer_horizontal_[l]?ys:xs;size_t count=lines.size()-1;
        if(count>remaining/sizeof(int))continue;
        auto&values=axis_capacity_[l];values.resize(count,0);remaining-=count*sizeof(int);
        char axis=layer_horizontal_[l]?'Y':'X';
        for(size_t k=0;k<count;k++){if(k%4096==0)check_time();for(const auto&t:d.tracks)if(t.layer==l&&t.axis==axis)values[k]+=count_tracks(t,lines[k],lines[k+1],k+1==count);}
    }
}
int Router::capacity(Id v)const{
    GR_COUNT(capacity_calls,1);
    int x,y,l;decode(v,x,y,l);
    if(static_frozen_&&l>=1&&l<=20&&!axis_capacity_[l].empty()){GR_COUNT(capacity_cache_hits,1);return axis_capacity_[l].at(layer_horizontal_[l]?y:x);}
    auto r=cell(v);auto&layer=d.layers.at(r.layer);char axis=layer.direction=="HORIZONTAL"?'Y':'X';int c=0;for(auto&t:d.tracks)if(t.layer==r.layer&&t.axis==axis)c+=count_tracks(t,axis=='X'?r.x1:r.y1,axis=='X'?r.x2:r.y2,axis=='X'?r.x2==xs.back():r.y2==ys.back());return c;
}
Resource& Router::resource(Id v){auto [it,newentry]=resources.try_emplace(v);if(newentry){GR_COUNT(resource_insertions,1);it->second.capacity=capacity(v);}return it->second;}
void Router::load_thermal(const std::filesystem::path&p){
    auto j=Json::parse(read_file(p));ambient=j.at("meta").at("ambientTemp").number();auto& nodes=j.at("nodes");if(nodes.type!=Json::Array||nodes.arr.empty())throw std::runtime_error("Thermal nodes must be a nonempty array");
    heat.assign(heat.size(),0);std::map<std::pair<double,double>,std::pair<double,int>> projected;
    for(auto&n:nodes.arr){if(n.type!=Json::Array||n.arr.size()!=4)throw std::runtime_error("Thermal node must be [x_um,y_um,z,temp]");double x=n.arr[0].number(),y=n.arr[1].number();n.arr[2].number();double temp=n.arr[3].number();auto&v=projected[{x,y}];v.first+=temp;v.second++;}
    for(auto&[xy,avg]:projected){double x=d.die.x1+xy.first*d.dbu,y=d.die.y1+xy.second*d.dbu;if(x<xs.front()||x>xs.back()||y<ys.front()||y>ys.back())continue;int k=locate(ys,y)*nx+locate(xs,x);heat[k]+=avg.first/avg.second;thermal_count[k]++;}
    int known=0;for(size_t k=0;k<heat.size();k++)if(thermal_count[k]){heat[k]/=thermal_count[k];known++;}if(!known)throw std::runtime_error("No thermal nodes mapped to DIEAREA");
    // Deterministic nearest valid value in previous column/row, then reverse fill at leading gaps.
    for(int y=0;y<ny;y++)for(int x=0;x<nx;x++){int k=y*nx+x;if(!thermal_count[k]){if(x&&std::isfinite(heat[k-1]))heat[k]=heat[k-1];else if(y)heat[k]=heat[k-nx];else heat[k]=std::numeric_limits<double>::quiet_NaN();}}
    double next=ambient;for(size_t k=heat.size();k-->0;){if(std::isfinite(heat[k]))next=heat[k];else heat[k]=next;}
    refresh_heat_stats();
    Json e=Json::object();e["mapped_gcells"]=known;e["total_gcells"]=nx*ny;e["ambient"]=ambient;trace.event("thermal_mapped",e);
    if(cfg.v3_stage>=3)build_v3_thermal(j);
}
void Router::build_fixed_demand(){
    for(auto&[name,i]:d.instances){check_time();auto&m=d.macros.at(i.macro);auto add=[&](const Rect&raw){
        if(!d.layers.count(raw.layer))return;
        auto r=placed_rect(raw,m,i,d.dbu);if(r.x2<xs.front()||r.y2<ys.front()||r.x1>xs.back()||r.y1>ys.back())return;
        r.x1=std::max(r.x1,xs.front());r.y1=std::max(r.y1,ys.front());r.x2=std::min(r.x2,xs.back());r.y2=std::min(r.y2,ys.back());
        int xa=locate(xs,r.x1),xb=locate(xs,std::nextafter(r.x2,r.x1)),ya=locate(ys,r.y1),yb=locate(ys,std::nextafter(r.y2,r.y1));
        for(int y=ya;y<=yb;y++)for(int x=xa;x<=xb;x++){
            auto v=id(x,y,r.layer);auto c=cell(v);char axis=d.layers.at(r.layer).direction=="HORIZONTAL"?'Y':'X';int n=0;
            for(auto&t:d.tracks)if(t.layer==r.layer&&t.axis==axis)n+=count_tracks(t,axis=='X'?std::max(c.x1,r.x1):std::max(c.y1,r.y1),axis=='X'?std::min(c.x2,r.x2):std::min(c.y2,r.y2),false);
            if(n)resource(v).fixed+=n;
        }
    };for(auto&r:m.obs)add(r);for(auto&[pn,rs]:m.pins)for(auto&r:rs)add(r);}
    trace.event("fixed_demand_built");
}
std::vector<Id> Router::search(Id start,Id goal,int lo,int hi,bool cross){
    return search_impl(start,goal,lo,hi,cross,nullptr);
}
void Router::add_target(TreeTarget&t,Id v)const{
    int x,y,l;decode(v,x,y,l);
    if(x<0||x>=nx||y<0||y>=ny||!d.layers.count(l))throw std::runtime_error("Invalid tree target");
    bool first=t.nodes.empty();if(!t.nodes.insert(v).second)return;
    if(first){t.xl=t.xh=x;t.yl=t.yh=y;t.ll=t.lh=l;}
    else{t.xl=std::min(t.xl,x);t.xh=std::max(t.xh,x);t.yl=std::min(t.yl,y);t.yh=std::max(t.yh,y);t.ll=std::min(t.ll,l);t.lh=std::max(t.lh,l);}
}
std::vector<Id> Router::search_to_tree(Id start,const std::unordered_set<Id>&nodes,int lo,int hi,bool cross){
    TreeTarget tree;for(Id v:nodes)add_target(tree,v);
    return search_impl(start,0,lo,hi,cross,&tree);
}
std::vector<Id> Router::search_impl(Id start,Id goal,int lo,int hi,bool cross,const TreeTarget*tree){
    GR_COUNT(astar_calls,1);GR_TIMER(astar_seconds);
    Progress::Scope log(progress,"A_STAR",true);
    if(tree){
        GR_COUNT(tree_searches,1);GR_MAX(tree_target_nodes_max,tree->nodes.size());check_time();
        int x,y,l;decode(start,x,y,l);
        if(tree->nodes.empty()||lo>hi||lo<1||hi>20||tree->ll<lo||tree->lh>hi||l<lo||l>hi||x>=nx||y>=ny||!d.layers.count(l))throw std::runtime_error("Tree search requires nonempty targets in one legal layer range");
        if(lo<=10&&hi>=11)throw std::runtime_error("Tree search cannot cross the Die interface");
    }
    if(tree?tree->nodes.count(start):start==goal)return {start};
    if(!static_frozen_)refresh_layers(); // Direct test/API calls may edit Design.
    int ax,ay,al,bx,by,bl;decode(start,ax,ay,al);decode(goal,bx,by,bl);
    if(tree){bx=tree->xl;by=tree->yl;bl=tree->ll;}
    auto [gx,gy]=center(tree?*tree->nodes.begin():goal);
    auto interval=[](double value,double low,double high){return std::max({low-value,0.0,value-high});};
    auto heuristic=[&](int x,int y,int l){
        if(tree&&tree->exact)return tree->exact->distance({cfg.wire*x_centers_[x]/unit,cfg.wire*y_centers_[y]/unit,cfg.via*l});
        if(tree)return cfg.wire*(interval(x_centers_[x],x_centers_[tree->xl],x_centers_[tree->xh])+interval(y_centers_[y],y_centers_[tree->yl],y_centers_[tree->yh]))/unit+cfg.via*interval(l,tree->ll,tree->lh);
        return cfg.wire*(std::abs(x_centers_[x]-gx)+std::abs(y_centers_[y]-gy))/unit+cfg.via*std::abs(l-bl);
    };
    struct State{double f,g;Id v;bool operator<(const State&o)const{return f!=o.f?f>o.f:v>o.v;}};
    for(int margin=cfg.margin;;margin=std::min(std::max(nx,ny),margin*2)){
        GR_COUNT(margin_attempts,1);
        check_time();int xl=std::max(0,std::min(ax,bx)-margin),xh=std::min(nx-1,std::max(ax,bx)+margin),yl=std::max(0,std::min(ay,by)-margin),yh=std::min(ny-1,std::max(ay,by)+margin);
        if(tree){xh=std::min(nx-1,std::max(ax,tree->xh)+margin);yh=std::min(ny-1,std::max(ay,tree->yh)+margin);}
        size_t width=size_t(xh-xl+1),height=size_t(yh-yl+1);auto layers=std::int64_t(hi)-lo+1;
        bool dense=layers>0&&layers<=20&&al>=lo&&al<=hi&&bl>=lo&&bl<=hi&&width*height<=dense_limit_/size_t(layers);
        auto slot=[&](int x,int y,int l){return (size_t(l-lo)*height+size_t(y-yl))*width+size_t(x-xl);};
        std::unordered_map<Id,SearchInfo> dist;
        if(dense){
            size_t count=width*height*size_t(layers);
            if(search_cells_.size()<count)std::vector<SearchCell>(count).swap(search_cells_);
            if(++search_generation_==0){for(auto&entry:search_cells_)entry.generation=0;search_generation_=1;}
            auto&entry=search_cells_[slot(ax,ay,al)];entry.info={0,start};entry.generation=search_generation_;
            GR_COUNT(dense_attempts,1);GR_MAX(max_workspace_bytes,search_cells_.capacity()*sizeof(SearchCell));
        }else{dist.reserve(4096);dist.emplace(start,SearchInfo{0,start});GR_COUNT(sparse_attempts,1);}
        auto known=[&](Id v)->const SearchInfo&{if(!dense)return dist.at(v);int x,y,l;decode(v,x,y,l);return search_cells_[slot(x,y,l)].info;};
        std::priority_queue<State> q;q.push({heuristic(ax,ay,al),0,start});size_t expanded=0,discovered=1;
        while(!q.empty()){
            auto cur=q.top();q.pop();GR_COUNT(queue_pops,1);if(cur.g>known(cur.v).g+1e-10){GR_COUNT(stale_pops,1);continue;}
            if(tree?tree->nodes.count(cur.v):cur.v==goal){std::vector<Id> result;for(Id v=cur.v;;v=known(v).parent){result.push_back(v);if(v==start)break;}std::reverse(result.begin(),result.end());return result;}
            if(tree&&tree->exact){++v3_counts_["multi_target_expansions"];if(expanded>=50000)throw V3Budget();}
            ++expanded;GR_COUNT(expansions,1);GR_MAX(max_expansions,expanded);if(expanded>cfg.search_limit)throw std::runtime_error("A* expansion limit; increase search_limit");
            if(expanded%4096==0)check_time();
            int x,y,l;decode(cur.v,x,y,l);
            double cx=x_centers_[x],cy=y_centers_[y];
            auto visit=[&](int xx,int yy,int ll){
                if(xx<xl||xx>xh||yy<yl||yy>yh||ll<lo||ll>hi)return;
                if(ll>=1&&ll<=20?!layer_present_[ll]:!d.layers.count(ll))return;
                Id v=id(xx,yy,ll);bool planar=l==ll;
                if(!planar){bool via=l>=1&&l<=20&&ll>=1&&ll<=20?(ll>l?via_up_[l]:via_down_[l]):d.vias.count({std::min(l,ll),std::max(l,ll)});if(!via)return;}
                if(planar&&cross&&(l==10||l==11))return; // Only access cells on interface layers.
                auto&res=resource(v);
                int demand=cross&&(ll==10||ll==11)?2:1;
                if(cfg.v3_batch&&cross&&(ll==10||ll==11))demand=1+int(v3_forced_tsv_&&(ll==10?v:id(xx,yy,10))==v3_forced_tsv_);
                double penalty=cfg.v3_stage>=2&&rerouting_&&!v3_legacy_route_?v3_penalty(v,demand):congestion_penalty(res,cross&&(ll==10||ll==11)?2:1);
                if(tree&&tree->exact&&tree->nodes.count(v))penalty=0;
                double base=planar?cfg.wire*(std::abs(cx-x_centers_[xx])+std::abs(cy-y_centers_[yy]))/unit:cfg.via;
                double cost=cur.g+base+penalty;bool improved=false;
                if(dense){auto&entry=search_cells_[slot(xx,yy,ll)];if(entry.generation!=search_generation_||cost+1e-10<entry.info.g){if(entry.generation!=search_generation_)++discovered;entry.info={cost,cur.v};entry.generation=search_generation_;improved=true;}}
                else{auto it=dist.find(v);if(it==dist.end()){dist.emplace(v,SearchInfo{cost,cur.v});++discovered;improved=true;}else if(cost+1e-10<it->second.g){it->second={cost,cur.v};improved=true;}}
                if(improved){GR_MAX(max_dist_nodes,discovered);q.push({cost+heuristic(xx,yy,ll),cost,v});}
            };
            bool horizontal=l>=1&&l<=20&&layer_present_[l]?layer_horizontal_[l]:d.layers.at(l).direction=="HORIZONTAL";
            if(horizontal){visit(x-1,y,l);visit(x+1,y,l);}else{visit(x,y-1,l);visit(x,y+1,l);}visit(x,y,l-1);visit(x,y,l+1);
        }
        if(xl==0&&yl==0&&xh==nx-1&&yh==ny-1)break;
    }
    throw std::runtime_error("No legal path between GCells "+std::to_string(start)+" and "+std::to_string(goal));
}
std::vector<std::pair<int,int>> Router::mst(const std::vector<Id>&ps)const{
    Progress::Scope log(progress,"MST",true);
    int n=int(ps.size());std::vector<double> best(n,1e100);std::vector<int> parent(n,-1);std::vector<bool> seen(n,false);std::vector<std::pair<int,int>> tree;if(n==0)return tree;best[0]=0;
    for(int k=0;k<n;k++){if(progress&&k%256==0)progress->poll();int u=-1;for(int v=0;v<n;v++)if(!seen[v]&&(u==-1||best[v]<best[u]))u=v;seen[u]=true;if(parent[u]>=0)tree.emplace_back(parent[u],u);auto[a,b]=center(ps[u]);int x,y,l;decode(ps[u],x,y,l);
        for(int v=0;v<n;v++)if(!seen[v]){auto[c,e]=center(ps[v]);int xx,yy,ll;decode(ps[v],xx,yy,ll);double w=cfg.wire*(std::abs(a-c)+std::abs(b-e))/unit+cfg.via*std::abs(l-ll);if(w<best[v]){best[v]=w;parent[v]=u;}}
    }return tree;
}
Id Router::select_tsv(const Net&n){
    GR_COUNT(tsv_calls,1);GR_TIMER(tsv_seconds);
    Progress::Scope log(progress,"TSV_SELECTION",true);
    if(!d.layers.count(10)||!d.layers.count(11)||!d.tsv_cut)throw std::runtime_error("Cross-die net requires M10/M11 and TSV CUT definition");
    std::set<std::pair<int,int>> low,up;std::vector<std::pair<int,int>> pins;int xl=nx-1,xh=0,yl=ny-1,yh=0;
    for(auto&p:n.pins){int x=locate(xs,p.x),y=locate(ys,p.y);pins.emplace_back(x,y);xl=std::min(xl,x);xh=std::max(xh,x);yl=std::min(yl,y);yh=std::max(yh,y);if(p.layer==10)low.insert({x,y});if(p.layer==11)up.insert({x,y});}
    std::vector<std::pair<int,int>> shared;std::set_intersection(low.begin(),low.end(),up.begin(),up.end(),std::back_inserter(shared));
    if(shared.size()>1)throw std::runtime_error("Multiple fixed TSV pin GCells require explicit disambiguation: "+n.name);
    if(!shared.empty())return id(shared[0].first,shared[0].second,10);
    // Heat is immutable inside run(). Outside it, callers may edit the public
    // vector (including retained references), so refresh on every direct call.
    if(!static_frozen_)refresh_heat_stats();
    double tmin=heat_min_,tmax=heat_max_,score=1e100;Id answer=0;
    int stride=std::max(1,int(std::ceil(std::sqrt(double((xh-xl+1)*(yh-yl+1))/4096))));
    std::vector<std::pair<int,int>> candidates;
    candidates.reserve(pins.size()+size_t((xh-xl)/stride+1)*size_t((yh-yl)/stride+1));
    candidates.insert(candidates.end(),pins.begin(),pins.end());for(int y=yl;y<=yh;y+=stride)for(int x=xl;x<=xh;x+=stride)candidates.emplace_back(x,y);
    std::sort(candidates.begin(),candidates.end());candidates.erase(std::unique(candidates.begin(),candidates.end()),candidates.end());
    size_t candidate_index=0;for(auto[x,y]:candidates){GR_COUNT(tsv_candidates,1);if(progress&&candidate_index++%256==0)progress->poll();double w=0;auto[cx,cy]=center(id(x,y,10));for(auto&p:n.pins)w+=(std::abs(p.x-cx)+std::abs(p.y-cy))/unit;w*=cfg.wire/std::max(size_t(1),n.pins.size());
        auto&a=resource(id(x,y,10));auto&b=resource(id(x,y,11));w+=cfg.tsv+cfg.congestion*(std::max(0,a.usage+a.fixed+2-a.capacity)+std::max(0,b.usage+b.fixed+2-b.capacity));
        // Nonnegative penalty for cold sites; positive heat/TSV correlation is desired.
        w+=cfg.thermal*(tmax>tmin?(tmax-heat[y*nx+x])/(tmax-tmin):0);
        if(w<score){score=w;answer=id(x,y,10);}
    }return answer;
}
Route Router::route(const Net&n){
    if(cfg.v3_stage&&!v3_legacy_route_){
        bool fallback=false;double previous=v3_route_deadline_;v3_route_deadline_=elapsed()+0.5;
        struct Reset {double&v;double old;~Reset(){v=old;}} reset{v3_route_deadline_,previous};
        if(n.pins.size()>16){++v3_fallbacks_["high_pin_count"];fallback=true;}
        else try{
            auto candidate=route_steiner(n);
            if(cfg.v3_stage>=2&&n.pins.size()>=3&&!v3_batch_active_){
                v3_route_deadline_=previous;v3_legacy_route_=true;
                struct LegacyFlag {bool&v;~LegacyFlag(){v=false;}} flag{v3_legacy_route_};
                auto reference=route(n);double cost=guide_grid_cost(candidate),old_cost=guide_grid_cost(reference);
                double phi=v3_marginal(candidate),old_phi=v3_marginal(reference),upper=v3_marginal(candidate,1),old_upper=v3_marginal(reference,1);
                if(!std::isfinite(cost)||!std::isfinite(old_cost)||cost>old_cost+1e-6||phi>old_phi+1e-10||upper>old_upper+1e-10||!(cost<old_cost-1e-6||phi<old_phi-1e-10)){++v3_fallbacks_["joint_guide_and_congestion_guard"];++v3_counts_["fallback_to_mst"];return reference;}
            }
            ++v3_counts_["new_routes_selected"];return candidate;
        }
        catch(const RouterTimeout&){++v3_fallbacks_["global_timeout"];throw;}
        catch(const RerouteBudget&){throw;}
        catch(const V3Budget&){++v3_fallbacks_["per_net_budget"];fallback=true;}
        catch(const std::runtime_error&e){std::string m=e.what();if(m.find("No legal path")==0||m.find("A* expansion limit")==0){++v3_fallbacks_["unreachable_or_expansion_limit"];fallback=true;}else throw;}
        if(fallback){++v3_counts_["fallback_to_mst"];v3_route_deadline_=previous;v3_legacy_route_=true;struct Flag {bool&v;~Flag(){v=false;}} flag{v3_legacy_route_};return route(n);}
    }
    Progress::Scope log(progress,"NET_ROUTE",true);
    Route r;r.cross=n.cross;Json sel;
    if(trace.out&&!trace.truncated){sel=Json::object();sel["net"]=n.name;sel["pin_count"]=int(n.pins.size());trace.event("net_selected",sel);}
    std::vector<Id> bottom,top;for(auto&p:n.pins){Id v=pin_node(p);(p.layer<=10?bottom:top).push_back(v);r.nodes.insert(v);}
    if(n.cross){r.tsv=v3_forced_tsv_?v3_forced_tsv_:select_tsv(n);bottom.push_back(r.tsv);int x,y,l;decode(r.tsv,x,y,l);Id v=id(x,y,11);top.push_back(v);r.nodes.insert(r.tsv);r.nodes.insert(v);r.edges.insert({r.tsv,v});}
    auto connect=[&](std::vector<Id>&ps,int lo,int hi){std::sort(ps.begin(),ps.end());ps.erase(std::unique(ps.begin(),ps.end()),ps.end());
        if(cfg.tree_route_mode==1){
            // Only the connected component is a target: r.nodes also holds
            // unconnected Pins and the other Die, so it must never be used here.
            TreeTarget target;
            size_t branches=0;
            Id seed=ps.front();
            if(n.cross){int x,y,l;decode(r.tsv,x,y,l);seed=lo==1?r.tsv:id(x,y,11);}
            add_target(target,seed);
            if(trace.out&&!trace.truncated){Json topo=sel;topo["tier"]=lo==1?"bottom":"top";topo["strategy"]="tree_aware";topo["terminal_nodes"]=numbers(ps);topo["mst_edges"]=Json::array();topo["seed_node"]=double(seed);trace.event("topology",topo);}
            while(true){
                check_time();Id next=0;bool found=false;double best=0;
                // O(P) bbox-distance selection, stable Node-ID tie breaker.
                // The tree bbox is updated with each new path, never scanned
                // in the A* expansion loop or copied between branch searches.
                for(Id terminal:ps){
                    if(target.nodes.count(terminal))continue;
                    int x,y,l;decode(terminal,x,y,l);
                    auto distance=[](double v,double a,double b){return std::max({a-v,0.0,v-b});};
                    double estimate=cfg.wire*(distance(x_centers_[x],x_centers_[target.xl],x_centers_[target.xh])+distance(y_centers_[y],y_centers_[target.yl],y_centers_[target.yh]))/unit+cfg.via*distance(l,target.ll,target.lh);
                    if(!found||estimate<best||(estimate==best&&terminal<next)){found=true;best=estimate;next=terminal;}
                }
                if(!found)break;
                Json ev;if(trace.out&&!trace.truncated){ev=sel;ev["strategy"]="tree_aware";ev["from"]=double(next);ev["target_count"]=double(target.nodes.size());trace.event("search_started",ev);}
                auto path=search_impl(next,0,lo,hi,n.cross,&target);
                ++branches;
                if(trace.out&&!trace.truncated){ev["to"]=double(path.back());ev["nodes"]=numbers(path);trace.event("path_found",ev);}
                for(Id v:path){r.nodes.insert(v);add_target(target,v);}
                for(size_t i=1;i<path.size();i++)r.edges.insert({std::min(path[i-1],path[i]),std::max(path[i-1],path[i])});
            }
            GR_COUNT(tree_terminal_skips,ps.size()-1-branches);(void)branches;
            return;
        }
        auto tree=mst(ps);
        if(trace.out&&!trace.truncated){Json topo=sel;topo["tier"]=lo==1?"bottom":"top";topo["terminal_nodes"]=numbers(ps);Json ts=Json::array();for(auto[a,b]:tree){Json e=Json::array();e.push(double(ps[a]));e.push(double(ps[b]));ts.push(e);}topo["mst_edges"]=ts;trace.event("topology",topo);}
        for(auto[a,b]:tree){check_time();Json ev;
            if(trace.out&&!trace.truncated){ev=sel;ev["from"]=double(ps[a]);ev["to"]=double(ps[b]);trace.event("search_started",ev);}
            auto p=search(ps[a],ps[b],lo,hi,n.cross);
            if(trace.out&&!trace.truncated){ev["nodes"]=numbers(p);trace.event("path_found",ev);}
            for(Id v:p)r.nodes.insert(v);
            for(size_t i=1;i<p.size();i++)r.edges.insert({std::min(p[i-1],p[i]),std::max(p[i-1],p[i])});}
    };if(!bottom.empty())connect(bottom,1,10);if(!top.empty())connect(top,11,20);
    make_guides(r);validate(n,r);return r;
}
void Router::occupy(const Route&r,int sign){for(Id v:r.nodes){auto&res=resource(v);res.usage+=sign;if(res.usage<0)throw std::runtime_error("Negative resource usage");}if(r.cross){int x,y,l;decode(r.tsv,x,y,l);resource(r.tsv).usage+=sign;resource(id(x,y,11)).usage+=sign;}}
long long Router::overflow()const{long long total=0;for(auto&[v,r]:resources)total+=std::max(0,r.fixed+r.usage-r.capacity);return total;}
double Router::wirelength(const Route&r)const{double w=0;for(auto&e:r.edges){int ax,ay,al,bx,by,bl;decode(e.a,ax,ay,al);decode(e.b,bx,by,bl);if(al==bl){auto[x,y]=center(e.a);auto[u,v]=center(e.b);w+=std::abs(x-u)+std::abs(y-v);}}return w;}
double Router::guide_wirelength(const Route&r)const{
    // Guide-contact MST proxy, not a replacement for the official evaluator.
    // Bounded O(G^2) work / O(G) memory. Unknown estimates reject a candidate.
    const auto&g=r.guides;size_t n=g.size();
    if(!n||n>1024)return std::numeric_limits<double>::quiet_NaN();
    std::vector<double> best(n,1e100);std::vector<int> parent(n,-1);std::vector<bool> seen(n,false);best[0]=0;double total=0;
    for(size_t k=0;k<n;k++){
        if(k%64==0){if(progress)progress->poll();if(elapsed()>cfg.time_limit)throw RouterTimeout();}
        int u=-1;for(size_t i=0;i<n;i++)if(!seen[i]&&(u<0||best[i]<best[u]||(best[i]==best[u]&&int(i)<u)))u=int(i);
        if(u<0||best[u]>=1e100)return std::numeric_limits<double>::quiet_NaN();
        seen[u]=true;
        if(parent[u]>=0){const auto&a=g[parent[u]];const auto&b=g[u];
            if(a.layer!=b.layer)total+=best[u];
            else if(d.layers.at(a.layer).direction=="HORIZONTAL")total+=std::max(a.x2,b.x2)-std::min(a.x1,b.x1);
            else total+=std::max(a.y2,b.y2)-std::min(a.y1,b.y1);
        }
        for(size_t v=0;v<n;v++)if(!seen[v]){const auto&a=g[u];const auto&b=g[v];
            if(std::abs(a.layer-b.layer)>1||a.x1>b.x2+1e-6||b.x1>a.x2+1e-6||a.y1>b.y2+1e-6||b.y1>a.y2+1e-6)continue;
            double dx=std::min(a.x2,b.x2)-std::max(a.x1,b.x1),dy=std::min(a.y2,b.y2)-std::max(a.y1,b.y1);
            if(a.layer!=b.layer){if(dx<=0||dy<=0)continue;}
            else if(d.layers.at(a.layer).direction=="HORIZONTAL"?dy<=0:dx<=0)continue;
            double cost=std::abs((a.x1+a.x2-b.x1-b.x2)/2)+std::abs((a.y1+a.y2-b.y1-b.y2)/2);
            if(a.layer!=b.layer&&std::min(a.layer,b.layer)==10)cost+=6; // Latest report's TSV proxy cost; separately calibrated.
            if(cost<best[v]||(cost==best[v]&&(parent[v]<0||u<parent[v]))){best[v]=cost;parent[v]=u;}
        }
    }return total;
}
void Router::build_guide_model(){
    fixed_intervals_.clear();guide_model_ready_=false;guide_interval_count_=0;
    constexpr size_t limit=2000000;bool bounded=true;
    for(const auto&[name,i]:d.instances){
        check_time();const auto&m=d.macros.at(i.macro);
        auto add=[&](const Rect&raw){
            if(!bounded||!d.layers.count(raw.layer))return;
            auto r=placed_rect(raw,m,i,d.dbu);
            if(r.x2<xs.front()||r.y2<ys.front()||r.x1>xs.back()||r.y1>ys.back())return;
            r.x1=std::max(r.x1,xs.front());r.x2=std::min(r.x2,xs.back());r.y1=std::max(r.y1,ys.front());r.y2=std::min(r.y2,ys.back());
            bool horizontal=d.layers.at(r.layer).direction=="HORIZONTAL";
            const auto&axis=horizontal?xs:ys;double lo=horizontal?r.x1:r.y1,hi=horizontal?r.x2:r.y2;
            int a=locate(axis,lo),b=locate(axis,std::nextafter(hi,lo));
            for(int k=a;k<=b;k++){
                if(++guide_interval_count_>limit){bounded=false;return;}
                fixed_intervals_[{r.layer,k}].push_back(horizontal?std::make_pair(r.y1,r.y2):std::make_pair(r.x1,r.x2));
            }
        };
        for(const auto&r:m.obs)add(r);
        for(const auto&[pin,rs]:m.pins)for(const auto&r:rs)add(r);
        if(!bounded){fixed_intervals_.clear();return;} // Keep original demand as explicit fallback.
    }
    for(auto&[key,intervals]:fixed_intervals_){
        check_time();std::sort(intervals.begin(),intervals.end());size_t count=0;
        for(auto interval:intervals){if(count&&interval.first<=intervals[count-1].second)intervals[count-1].second=std::max(intervals[count-1].second,interval.second);else intervals[count++]=interval;}
        intervals.resize(count);
    }
    guide_model_ready_=true;
}
int Router::guide_fixed_demand(Id v){
    auto&res=resource(v);if(!guide_model_ready_)return res.fixed;int demand=0;
    int x,y,l;decode(v,x,y,l);bool horizontal=d.layers.at(l).direction=="HORIZONTAL";
    auto found=fixed_intervals_.find({l,horizontal?x:y});if(found==fixed_intervals_.end())return 0;
    const auto&axis=horizontal?ys:xs;int index=horizontal?y:x;double lo=axis[index],hi=axis[index+1];char track_axis=horizontal?'Y':'X';
    const auto&intervals=found->second;
    auto it=std::lower_bound(intervals.begin(),intervals.end(),lo,[](const auto&a,double b){return a.second<b;});
    for(;it!=intervals.end()&&it->first<=hi;++it)for(const auto&t:d.tracks)if(t.layer==l&&t.axis==track_axis)demand+=count_tracks(t,std::max(lo,it->first),std::min(hi,it->second),true);
    return demand;
}
void Router::prune_routes(){
    // Use legal adjacency inside the already occupied cells, then Kruskal +
    // nonterminal leaf peel. Adjacent occupied cells can have a legal shortcut
    // absent from the original MST branches. No new cell or TSV is introduced.
    struct Backup {size_t index;Route old;bool applied=false;};std::vector<Backup> saved;
    size_t attempted=0,accepted=0,removed=0,backup_nodes=0;double deadline=elapsed()+20;std::string stop="nets_exhausted";
    auto restore=[&](Backup&b){if(!b.applied)return;occupy(routes[b.index],-1);routes[b.index]=std::move(b.old);occupy(routes[b.index],1);b.applied=false;};
    try{
        for(size_t index=0;index<routes.size();index++){
            check_time();if(elapsed()>=deadline){stop="time_budget";break;}if(attempted>=512){stop="candidate_budget";break;}
            const auto&old=routes[index];
            if(old.nodes.size()<2||old.nodes.size()>100000)continue;
            std::set<Edge> induced=old.edges;size_t scanned=0;
            for(Id v:old.nodes){
                if(++scanned%4096==0)check_time();
                int x,y,l;decode(v,x,y,l);bool horizontal=d.layers.at(l).direction=="HORIZONTAL";
                if(horizontal?x+1<nx:y+1<ny){Id w=horizontal?id(x+1,y,l):id(x,y+1,l);if(old.nodes.count(w))induced.insert({v,w});}
                if(l<20&&(l==10?(old.cross&&v==old.tsv):d.vias.count({l,l+1}))){Id w=id(x,y,l+1);if(old.nodes.count(w))induced.insert({v,w});}
            }
            if(induced.size()<old.nodes.size())continue;
            ++attempted;double old_cost=cfg.v3_stage?guide_grid_cost(old):guide_wirelength(old);if(!std::isfinite(old_cost))continue;
            std::vector<Id> nodes(old.nodes.begin(),old.nodes.end());std::unordered_map<Id,size_t> slots;for(size_t k=0;k<nodes.size();k++)slots[nodes[k]]=k;
            std::vector<size_t> parent(nodes.size()),rank(nodes.size());std::iota(parent.begin(),parent.end(),0);
            auto root=[&](size_t k){while(parent[k]!=k){parent[k]=parent[parent[k]];k=parent[k];}return k;};
            struct Weighted {double cost;Edge edge;};std::vector<Weighted> edges;edges.reserve(induced.size());
            size_t examined=0;for(const auto&e:induced){
                if(++examined%4096==0)check_time();
                int x,y,l,xx,yy,ll;decode(e.a,x,y,l);decode(e.b,xx,yy,ll);auto[a,b]=center(e.a);auto[c,distance_y]=center(e.b);
                double weight=l==ll?cfg.wire*(std::abs(a-c)+std::abs(b-distance_y))/unit:std::min(l,ll)==10?cfg.tsv:cfg.via;
                edges.push_back({weight,e});
            }
            std::sort(edges.begin(),edges.end(),[](const auto&a,const auto&b){return a.cost!=b.cost?a.cost<b.cost:a.edge<b.edge;});
            std::vector<std::vector<size_t>> adjacency(nodes.size());std::vector<std::pair<size_t,size_t>> tree;
            for(const auto&e:edges){size_t a=slots.at(e.edge.a),b=slots.at(e.edge.b),ra=root(a),rb=root(b);if(ra==rb)continue;
                if(rank[ra]<rank[rb]){std::swap(ra,rb);}
                parent[rb]=ra;if(rank[ra]==rank[rb])++rank[ra];
                tree.emplace_back(a,b);adjacency[a].push_back(b);adjacency[b].push_back(a);
            }
            std::vector<bool> mandatory(nodes.size(),false),active(nodes.size(),true);std::vector<size_t> degree(nodes.size());
            for(const auto&p:d.nets[index].pins)mandatory[slots.at(pin_node(p))]=true;
            if(old.cross){int x,y,l;decode(old.tsv,x,y,l);mandatory[slots.at(old.tsv)]=true;mandatory[slots.at(id(x,y,11))]=true;}
            std::queue<size_t> leaves;for(size_t k=0;k<nodes.size();k++){degree[k]=adjacency[k].size();if(degree[k]<=1&&!mandatory[k])leaves.push(k);}
            while(!leaves.empty()){size_t a=leaves.front();leaves.pop();if(!active[a]||mandatory[a]||degree[a]>1)continue;active[a]=false;
                for(size_t b:adjacency[a])if(active[b]){--degree[b];if(degree[b]<=1&&!mandatory[b])leaves.push(b);}
            }
            Route candidate;candidate.cross=old.cross;candidate.tsv=old.tsv;
            for(size_t k=0;k<nodes.size();k++)if(active[k])candidate.nodes.insert(nodes[k]);
            if(candidate.nodes.size()==old.nodes.size())continue;
            for(Id v:old.steiner_points)if(candidate.nodes.count(v))candidate.steiner_points.push_back(v);
            for(auto[a,b]:tree)if(active[a]&&active[b])candidate.edges.insert({std::min(nodes[a],nodes[b]),std::max(nodes[a],nodes[b])});
            make_guides(candidate);validate(d.nets[index],candidate);double estimate=cfg.v3_stage?guide_grid_cost(candidate):guide_wirelength(candidate);
            if(!std::isfinite(estimate)||estimate>old_cost+1e-8||candidate.guides.size()>old.guides.size())continue;
            if(backup_nodes+old.nodes.size()>100000){stop="backup_node_budget";break;}
            backup_nodes+=old.nodes.size();
            size_t reduction=old.nodes.size()-candidate.nodes.size();
            // Candidate is a strict occupied-node subset with the same TSV. Both
            // original fixed+usage and merged-fixed estimates cannot increase.
            saved.push_back({index,{}});auto&backup=saved.back();backup.old=std::move(routes[index]);
            routes[index]=std::move(candidate);occupy(backup.old,-1);occupy(routes[index],1);backup.applied=true;
            if(trace.out&&!trace.truncated){Json event=Json::object();event["net"]=d.nets[index].name;trace.event("net_removed",event);trace.event("net_added",route_json(d.nets[index],routes[index]));}
            ++accepted;removed+=reduction;
        }
        final_prune_stats_=Json::object();final_prune_stats_["attempted"]=double(attempted);final_prune_stats_["accepted"]=double(accepted);final_prune_stats_["removed_nodes"]=double(removed);final_prune_stats_["backup_nodes"]=double(backup_nodes);final_prune_stats_["backup_node_limit"]=100000;final_prune_stats_["stop_reason"]=stop;
        trace.event("guide_subset_pruned",final_prune_stats_);
    }catch(...){for(auto it=saved.rbegin();it!=saved.rend();++it)restore(*it);throw;}
}
void Router::make_guides(Route&r)const{
    Progress::Scope log(progress,"GUIDE_BUILD",true);
    // Interface-layer cells stay separate: never merge TSV candidates into long strips.
    std::set<Id> remaining=r.nodes;
    while(!remaining.empty()){Id a=*remaining.begin();remaining.erase(a);int x,y,l;decode(a,x,y,l);Rect box=cell(a);
        bool horizontal=d.layers.at(l).direction=="HORIZONTAL";if(!(r.cross&&(l==10||l==11))){int xx=x,yy=y;while(true){if(horizontal)++xx;else ++yy;if(xx>=nx||yy>=ny)break;Id b=id(xx,yy,l);if(!remaining.count(b))break;remaining.erase(b);auto c=cell(b);box.x2=c.x2;box.y2=c.y2;}}
        r.guides.push_back(box);
    }
}
void Router::validate(const Net&n,const Route&r)const{
    if(r.nodes.empty())throw std::runtime_error("Empty route: "+n.name);
    std::unordered_map<Id,std::vector<Id>> adj;for(auto&e:r.edges){adj[e.a].push_back(e.b);adj[e.b].push_back(e.a);int x,y,l,xx,yy,ll;decode(e.a,x,y,l);decode(e.b,xx,yy,ll);if(l==ll){if(std::abs(x-xx)+std::abs(y-yy)!=1)throw std::runtime_error("Nonadjacent planar edge");if((d.layers.at(l).direction=="HORIZONTAL"&&y!=yy)||(d.layers.at(l).direction=="VERTICAL"&&x!=xx))throw std::runtime_error("Nonpreferred direction");}else{if(x!=xx||y!=yy||std::abs(l-ll)!=1)throw std::runtime_error("Illegal via edge");if(std::min(l,ll)==10){if(!n.cross||e.a!=r.tsv)throw std::runtime_error("Unexpected TSV edge");}else if(!d.vias.count({std::min(l,ll),std::max(l,ll)}))throw std::runtime_error("Via absent in LEF");}}
    std::unordered_set<Id> visited;std::vector<Id> stack{*r.nodes.begin()};while(!stack.empty()){Id a=stack.back();stack.pop_back();if(!visited.insert(a).second)continue;for(Id b:adj[a])stack.push_back(b);}if(visited.size()!=r.nodes.size())throw std::runtime_error("Disconnected route: "+n.name);
    for(auto&p:n.pins)if(!r.nodes.count(pin_node(p)))throw std::runtime_error("Uncovered pin: "+p.name);
    for(auto&g:r.guides){if(g.x1<d.die.x1||g.y1<d.die.y1||g.x2>d.die.x2||g.y2>d.die.y2)throw std::runtime_error("Guide outside die");if(!n.cross&&((g.layer<=10)!=(n.pins.front().layer<=10)))throw std::runtime_error("Inner net crossed die");}
    if(n.cross){int overlaps=0;for(Id a:r.nodes){int x,y,l;decode(a,x,y,l);if(l==10&&r.nodes.count(id(x,y,11)))overlaps++;}if(overlaps!=1)throw std::runtime_error("Nonunique TSV overlap: "+n.name);}
}
std::vector<size_t> Router::initial_order()const{
    std::vector<size_t> order(d.nets.size());std::iota(order.begin(),order.end(),0);
    if(!cfg.net_order_mode)return order;
    std::vector<double> hpwl(d.nets.size());size_t scanned=0;
    for(size_t i=0;i<d.nets.size();i++){
        check_time();const auto&pins=d.nets[i].pins;if(pins.empty())continue;
        double xl=pins.front().x,xh=xl,yl=pins.front().y,yh=yl;
        for(const auto&p:pins){xl=std::min(xl,p.x);xh=std::max(xh,p.x);yl=std::min(yl,p.y);yh=std::max(yh,p.y);if(++scanned%4096==0)check_time();}
        hpwl[i]=xh-xl+yh-yl;
    }
    std::sort(order.begin(),order.end(),[&](size_t a,size_t b){
        if(cfg.net_order_mode==2){
            if(d.nets[a].cross!=d.nets[b].cross)return d.nets[a].cross;
            if(d.nets[a].pins.size()!=d.nets[b].pins.size())return d.nets[a].pins.size()>d.nets[b].pins.size();
        }
        return hpwl[a]!=hpwl[b]?hpwl[a]>hpwl[b]:a<b;
    });check_time();return order;
}
std::vector<size_t> Router::reroute_order()const{
    std::vector<size_t> order(routes.size());std::iota(order.begin(),order.end(),0);
    if(!cfg.reroute_priority_mode&&!cfg.reroute_policy_mode)return order;
    struct Priority {long long marginal=0;double severity=0;size_t hot=0;};
    std::vector<Priority> keys(routes.size());size_t scanned=0;
    for(size_t i=0;i<routes.size();i++){
        check_time();auto&r=routes[i];int x,y,l;decode(r.tsv,x,y,l);Id upper=id(x,y,11);
        std::unordered_set<Id> mandatory;
        if(cfg.reroute_policy_mode)for(const auto&p:d.nets[i].pins)mandatory.insert(pin_node(p));
        for(Id v:r.nodes){
            if(mandatory.count(v))continue;
            auto it=resources.find(v);if(it==resources.end())continue;const auto&res=it->second;
            long long excess=std::max(0LL,static_cast<long long>(res.usage)+res.fixed-res.capacity);
            if(excess){keys[i].marginal+=std::min(excess,r.cross&&(v==r.tsv||v==upper)?2LL:1LL);keys[i].severity+=double(excess)/std::max(1,res.capacity);keys[i].hot++;}
            if(++scanned%4096==0)check_time();
        }
    }
    std::sort(order.begin(),order.end(),[&](size_t a,size_t b){
        auto&u=keys[a];auto&v=keys[b];
        if(u.marginal!=v.marginal)return u.marginal>v.marginal;
        if(u.severity!=v.severity)return u.severity>v.severity;
        if(u.hot!=v.hot)return u.hot>v.hot;
        if(d.nets[a].cross!=d.nets[b].cross)return d.nets[a].cross;
        return a<b;
    });check_time();return order;
}
std::pair<long long,int> Router::guarded_reroute_pass(int iteration,long long best){
    auto order=reroute_order();
    if(reroute_cooldown_.size()!=routes.size())reroute_cooldown_.assign(routes.size(),0);
    double total_wire=0;for(const auto&r:routes)total_wire+=wirelength(r);
    if(routing_wire_reference_==0)routing_wire_reference_=total_wire;
    const double wire_limit=routing_wire_reference_*(1+cfg.reroute_wire_growth);
    const double previous_deadline=reroute_deadline_seconds_;
    struct DeadlineReset {double&value;double previous;~DeadlineReset(){value=previous;}} reset{reroute_deadline_seconds_,previous_deadline};
    reroute_deadline_seconds_=elapsed()+cfg.reroute_pass_seconds;
    struct Backup {size_t index;Route old;bool replacement=false,detached=true;};
    std::vector<Backup> saved;
    int attempted=0,accepted=0,rejected=0,skipped=0;std::string stop="candidates_exhausted";
    auto demand=[&](const Route&r,Id v){int x,y,l;decode(r.tsv,x,y,l);return r.cross&&(v==r.tsv||v==id(x,y,11))?2LL:1LL;};
    auto removal=[&](const Route&r){long long value=0;for(Id v:r.nodes){const auto&s=resources.at(v);value+=std::min(std::max(0LL,static_cast<long long>(s.usage)+s.fixed-s.capacity),demand(r,v));}return value;};
    auto insertion=[&](const Route&r){long long value=0;for(Id v:r.nodes){const auto&s=resources.at(v);long long u=static_cast<long long>(s.usage)+s.fixed-s.capacity;value+=std::max(0LL,u+demand(r,v))-std::max(0LL,u);}return value;};
    auto restore=[&](Backup&b){if(!b.detached)return;if(b.replacement)occupy(routes[b.index],-1);routes[b.index]=std::move(b.old);occupy(routes[b.index],1);b.detached=false;};
    auto trace_restored=[&](size_t i){if(trace.out&&!trace.truncated){Json rem=Json::object();rem["net"]=d.nets[i].name;trace.event("net_removed",rem);trace.event("net_added",route_json(d.nets[i],routes[i]));}};
    try{
        for(size_t position=0;position<order.size();position++){
            // The soft per-pass deadline stops A* as well as this outer loop.
            // The global Router budget always takes precedence and propagates.
            if(elapsed()>cfg.time_limit)throw RouterTimeout();
            if(elapsed()>=reroute_deadline_seconds_){stop="time_budget";break;}
            if(attempted>=cfg.reroute_max_nets){stop="net_budget";break;}
            size_t i=order[position];
            if(progress)progress->net(position+1,routes.size(),d.nets[i].name,iteration);
            if(reroute_cooldown_[i]>=iteration){++skipped;continue;}
            std::unordered_set<Id> mandatory;for(const auto&p:d.nets[i].pins)mandatory.insert(pin_node(p));
            bool avoidable=false;for(Id v:routes[i].nodes){const auto&s=resources.at(v);if(!mandatory.count(v)&&s.usage+s.fixed>s.capacity){avoidable=true;break;}}
            if(!avoidable){++skipped;continue;}
            long long removed=removal(routes[i]);double old_wire=wirelength(routes[i]);
            saved.push_back({i,{}});auto&backup=saved.back();backup.old=std::move(routes[i]);routes[i]=Route{};
            occupy(backup.old,-1);++attempted;GR_COUNT(rerouted_nets,1);
            if(trace.out&&!trace.truncated){Json rem=Json::object();rem["net"]=d.nets[i].name;rem["iteration"]=iteration;trace.event("net_removed",rem);}
            bool exhausted=false,failed=false;
            try{routes[i]=route(d.nets[i]);}
            catch(const RouterTimeout&){throw;}
            catch(const RerouteBudget&){exhausted=true;}
            catch(const std::bad_alloc&){throw;}
            catch(const std::runtime_error&e){std::string message=e.what();if(message.find("A* expansion limit")==0||message.find("No legal path between GCells")==0)failed=true;else throw;}
            if(!exhausted&&!failed){
                long long added=insertion(routes[i]);double new_wire=wirelength(routes[i]);
                // Exact union-of-Net occupancy delta; fixed demand cancels.
                // Require both a local overflow gain and bounded wire growth.
                if(added<removed&&new_wire<=old_wire*(1+cfg.reroute_wire_growth)+1e-8&&total_wire-old_wire+new_wire<=wire_limit+1e-8){
                    occupy(routes[i],1);backup.replacement=true;total_wire+=new_wire-old_wire;++accepted;
                    if(trace.out&&!trace.truncated)trace.event("net_added",route_json(d.nets[i],routes[i]));
                    if(progress)progress->completed();
                    continue;
                }
            }
            restore(backup);saved.pop_back();++rejected;reroute_cooldown_[i]=iteration+1;trace_restored(i);
            if(progress)progress->completed();
            if(exhausted){stop="time_budget";break;}
        }
        long long now=overflow();
        if(now>=best){
            for(auto it=saved.rbegin();it!=saved.rend();++it){restore(*it);trace_restored(it->index);}
            saved.clear();accepted=0;
        }
        last_reroute_diagnostics_=Json::object();
        last_reroute_diagnostics_["policy"]="guarded_contribution";last_reroute_diagnostics_["attempted"]=attempted;
        last_reroute_diagnostics_["accepted_nets"]=accepted;last_reroute_diagnostics_["rejected_nets"]=rejected;
        last_reroute_diagnostics_["skipped_nets"]=skipped;last_reroute_diagnostics_["stop_reason"]=stop;
        last_reroute_diagnostics_["wire_limit_dbu_local"]=wire_limit;
        return {now,attempted};
    }catch(...){
        for(auto it=saved.rbegin();it!=saved.rend();++it)restore(*it);
        throw;
    }
}
std::pair<long long,int> Router::reroute_pass(int it,long long best){
    check_time();for(auto&[v,res]:resources)if(res.usage&&res.usage+res.fixed>res.capacity)res.history+=double(res.usage+res.fixed-res.capacity)/std::max(1,res.capacity);
    bool previous=rerouting_;rerouting_=true;
    struct PhaseReset {bool&value;bool previous;~PhaseReset(){value=previous;}} phase{rerouting_,previous};
    if(cfg.reroute_policy_mode)return guarded_reroute_pass(it,best);
    struct Backup {size_t index;Route old;bool detached=false,replacement=false;};
    std::vector<Backup> saved;int rerouted=0;auto order=reroute_order();
    // All old/new nodes already have resources. Restoring valid committed
    // routes needs no resource insertion, allocation or Trace construction.
    auto restore=[&](Backup&b){
        if(!b.detached)return;
        if(b.replacement)occupy(routes[b.index],-1);
        routes[b.index]=std::move(b.old);GR_COUNT(route_move_count,1);
        occupy(routes[b.index],1);b.detached=false;GR_COUNT(rollback_nets,1);
    };
    try{
        for(size_t position=0;position<order.size();position++){
            size_t i=order[position];
            if(progress)progress->net(position+1,routes.size(),d.nets[i].name,it);
            bool hot=false;for(Id v:routes[i].nodes){auto&res=resource(v);if(res.usage+res.fixed>res.capacity){hot=true;break;}}
            if(!hot){if(progress)progress->completed();continue;}
            GR_COUNT(rerouted_nets,1);GR_TIMER(reroute_seconds);Progress::Scope reroute_log(progress,"RIP_UP_REROUTE",true);
            // Reserve the undo record before touching occupancy. A vector
            // allocation failure therefore leaves the current route intact.
            saved.push_back({i,{}});auto&backup=saved.back();
            {GR_TIMER(backup_seconds);backup.old=std::move(routes[i]);routes[i]=Route{};GR_COUNT(route_move_count,1);}
            occupy(backup.old,-1);backup.detached=true;
            if(trace.out&&!trace.truncated){Json rem=Json::object();rem["net"]=d.nets[i].name;rem["iteration"]=it;trace.event("net_removed",rem);}
            routes[i]=route(d.nets[i]);occupy(routes[i],1);backup.replacement=true;
            if(trace.out&&!trace.truncated)trace.event("net_added",route_json(d.nets[i],routes[i]));
            rerouted++;if(progress)progress->completed();
        }
        long long now;{Progress::Scope overflow_log(progress,"OVERFLOW");now=overflow();}
        if(now>=best){
            GR_TIMER(rollback_seconds);Progress::Scope rollback_log(progress,"ROLLBACK");size_t changed=0;
            // Keep progress as the original full Net scan, but restore and
            // emit events only for changed routes; untouched state stays valid.
            for(size_t position=0;position<order.size();position++){
                size_t i=order[position];
                if(progress)progress->net(position+1,routes.size(),d.nets[i].name,it);
                if(changed<saved.size()&&saved[changed].index==i){
                    restore(saved[changed++]);
                    if(trace.out&&!trace.truncated){Json rem=Json::object();rem["net"]=d.nets[i].name;trace.event("net_removed",rem);trace.event("net_added",route_json(d.nets[i],routes[i]));}
                }
                if(progress)progress->completed();
            }
        }
        return {now,rerouted};
    }catch(...){
        // Restore every route modified in this pass, including a detached TSV
        // route. Do not risk masking the original exception with Trace I/O.
        for(auto it=saved.rbegin();it!=saved.rend();++it)restore(*it);
        throw;
    }
}
void Router::run(){
    refresh_layers();refresh_heat_stats();refresh_capacities();bool previous_frozen=static_frozen_;static_frozen_=true;
    struct RestoreFlag {bool&value;bool previous;~RestoreFlag(){value=previous;}} restore_flag{static_frozen_,previous_frozen};
    if(progress)progress->pass(d.nets.size(),0);
    Progress::Scope routing_log(progress,"ROUTING");
    if(cfg.v3_stage>=2){Progress::Scope model(progress,"V3_FIXED_PROJECTION");build_guide_model();}
    auto order=initial_order();
    for(size_t position=0;position<order.size();position++){size_t i=order[position];if(progress)progress->net(position+1,d.nets.size(),d.nets[i].name,0);check_time();try{routes[i]=route(d.nets[i]);occupy(routes[i],1);if(trace.out&&!trace.truncated)trace.event("net_added",route_json(d.nets[i],routes[i]));if(progress)progress->completed();}catch(const RouterTimeout&){throw;}catch(const std::bad_alloc&){throw;}catch(const std::exception&e){throw std::runtime_error("Net "+d.nets[i].name+": "+e.what());}}
    int stagnant=0;long long best;{Progress::Scope overflow_log(progress,"OVERFLOW");best=overflow();}Json initial=Json::object();initial["iteration"]=0;initial["overflow_local"]=double(best);rounds.push_back(initial);trace.event("iteration_done",initial);
    if(cfg.v3_stage>=2){for(int it=1;it<=cfg.iterations;it++){if(progress)progress->pass(d.nets.size(),it);if(cfg.v3_batch){hotspot_round(it);if(v3_batch_rounds_.back().at("accepted_groups").number()==0)break;}else{negotiated_round(it);if(v3_rounds_.back().at("accepted_nets").number()==0)break;}}}
    else for(int it=1;it<=cfg.iterations&&best>0&&stagnant<cfg.stagnation;it++){
        if(progress)progress->pass(d.nets.size(),it);
        Progress::Scope iteration_log(progress,"OPTIMIZATION");
        auto[now,rerouted]=reroute_pass(it,best);bool accepted=now<best;
        if(!accepted)stagnant++;else{best=now;stagnant=0;}
        Json j=Json::object();j["iteration"]=it;j["candidate_overflow_local"]=double(now);j["overflow_local"]=double(best);j["accepted"]=accepted;j["rerouted_nets"]=rerouted;if(cfg.reroute_policy_mode)j["reroute_diagnostics"]=last_reroute_diagnostics_;rounds.push_back(j);trace.event("iteration_done",j);
    }
    if(cfg.v3_stage>=3)optimize_tsv_global();
    if(cfg.v3_final){
        {Progress::Scope pruning_log(progress,"GUIDE_PRUNE");prune_routes();}
    }
    Json end=Json::object();end["overflow_local"]=double(overflow());end["runtime_seconds"]=elapsed();trace.event("routing_complete",end);
}
Json Router::route_json(const Net&n,const Route&r)const{
    GR_COUNT(route_json_calls,1);
    Json j=Json::object();j["net"]=n.name;j["cross_die"]=n.cross;j["nodes"]=numbers(std::vector<Id>(r.nodes.begin(),r.nodes.end()));Json edges=Json::array();int vias=0;
    for(auto&e:r.edges){Json v=Json::array();v.push(double(e.a));v.push(double(e.b));edges.push(v);int x,y,l,xx,yy,ll;decode(e.a,x,y,l);decode(e.b,xx,yy,ll);if(l!=ll&&std::min(l,ll)!=10)vias++;}j["edges"]=edges;
    Json gs=Json::array();for(auto&r:r.guides){Json g=numbers(std::vector<double>{r.x1,r.y1,r.x2,r.y2,double(r.layer)});gs.push(g);}j["guides"]=gs;
    Json ps=Json::array();for(auto&p:n.pins){Json v=Json::object();v["name"]=p.name;v["x"]=p.x;v["y"]=p.y;v["layer"]=p.layer;v["node"]=double(pin_node(p));ps.push(v);}j["pins"]=ps;
    if(!r.steiner_points.empty())j["steiner_topology_nodes"]=numbers(r.steiner_points);
    j["wirelength_dbu_local"]=wirelength(r);j["via_count"]=vias;j["tsv_count"]=n.cross?1:0;j["tsv_node"]=n.cross?Json(double(r.tsv)):Json();j["status"]="ROUTED";return j;
}
void Router::result_json(std::ostream&o)const{
    const auto began=std::chrono::steady_clock::now();
    auto seconds=[&]{return std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();};
    auto checkpoint=[&](const char*section,size_t count,size_t total,const char*message=""){
        o.flush();if(!o)throw std::runtime_error("Result JSON stream failed while writing "+std::string(section));
        auto pos=o.tellp();if(progress)progress->output(section,count,total,pos<0?0:double(std::streamoff(pos)),seconds(),message);
        check_time();
    };
    bool first=true;o<<'{';
    auto key=[&](const char*name){if(!first)o<<',';first=false;o<<Json::quote(name)<<':';};
    auto field=[&](const char*name,const Json&v){key(name);v.write(o);};
    // One element at a time; no Json array mirrors a large design container.
    auto array=[&](const char*name,const auto&values,const auto&make,const char*section){
        check_time();key(name);o<<'[';size_t count=0;
        for(const auto&value:values){
            if(count)o<<',';
            {auto item=make(value);item.write(o);}
            ++count;if(count%1024==0)check_time();
            if(count%10000==0&&count!=values.size())checkpoint(section,count,values.size());
        }
        o<<']';
        // Small metadata arrays need no extra disk flush/log record. Large
        // arrays and the required Instance/Resource milestones remain visible.
        if(values.size()>=10000||std::string_view(section)=="Instances"||std::string_view(section)=="Resources")checkpoint(section,count,values.size(),"completed");
        else check_time();
    };
    auto number=[](auto v){return Json(double(v));};
    check_time();
    if(cfg.v3_stage)field("v3_algorithm",v3_metadata());
    field("schema_version",1);field("source","independent_cpp_router");field("design",d.name);field("dbu_per_micron",d.dbu);
    if(cfg.v3_final){Json final=Json::object();final["strategy"]=cfg.v3_batch?"v3_2_hotspot_multi_net":cfg.v3_stage?"v3_steiner_negotiated_global":"v3_final_guide_guard";final["optimization"]=cfg.v3_batch?"whole_group_negotiated_transaction":cfg.v3_stage?"bounded_hanan_exact_target_price_tsv":"v2_safe_reroute_then_occupied_graph_prune";final["pruning"]=final_prune_stats_;final["fixed_union_role"]=cfg.v3_stage>=2?"experimental_search_price_approximation":"offline_calibration_only";final["fixed_union_available"]=guide_model_ready_;final["input_intervals"]=double(guide_interval_count_);final["guide_estimator_max_rectangles"]=1024;final["official_score"]=false;field("v3_final",final);}
    if(cfg.v3_final||cfg.net_order_mode||cfg.tree_route_mode||cfg.reroute_priority_mode){Json modes=Json::object();modes["net_order_mode"]=cfg.net_order_mode;modes["tree_route_mode"]=cfg.tree_route_mode;modes["reroute_priority_mode"]=cfg.reroute_priority_mode;modes["routing_strategy"]=cfg.v3_stage?"steiner_and_multi_target_with_fallback":cfg.tree_route_mode?"tree_aware":"mst_astar";field("algorithm",modes);}
    if(cfg.v3_final||cfg.congestion_cost_mode||cfg.reroute_policy_mode){Json modes=Json::object();modes["active_cost_model"]=cfg.v3_stage>=2?"normalized_marginal_negotiated":"legacy_research_cost_mode";modes["cost_mode"]=cfg.congestion_cost_mode;modes["scope_mode"]=cfg.congestion_scope_mode;modes["reroute_policy_mode"]=cfg.reroute_policy_mode;modes["reroute_max_nets"]=cfg.reroute_max_nets;modes["reroute_pass_seconds"]=cfg.reroute_pass_seconds;modes["reroute_wire_growth"]=cfg.reroute_wire_growth;field("congestion_strategy",modes);}
    field("die",numbers(std::vector<double>{d.die.x1,d.die.y1,d.die.x2,d.die.y2}));
    array("x_lines",xs,number,"X lines");array("y_lines",ys,number,"Y lines");
    array("layers",d.layers,[](const auto&entry){const auto&[n,l]=entry;Json v=Json::object();v["number"]=n;v["name"]=l.name;v["tier"]=n<=10?"bottom":"top";v["direction"]=l.direction;return v;},"Layers");
    key("nets");o<<'[';double length=0;int tsv=0,vias=0;
    for(size_t i=0;i<routes.size();i++){
        check_time();if(i)o<<',';
        {auto v=route_json(d.nets[i],routes[i]);length+=v.at("wirelength_dbu_local").number();tsv+=routes[i].cross;vias+=int(v.at("via_count").number());v.write(o);}
        // The current Net's Json is destroyed before logging/sampling.
        if((i+1)%1000==0&&i+1!=routes.size())checkpoint("Nets",i+1,routes.size());
    }
    o<<']';checkpoint("Nets",routes.size(),routes.size(),"completed");
    array("instances",d.instances,[&](const auto&entry){
        const auto&[name,i]=entry;auto&m=d.macros.at(i.macro);auto rect=placed_rect({0,0,m.width,m.height,0},m,i,d.dbu);
        Json v=Json::object();v["name"]=name;v["macro"]=i.macro;v["orientation"]=i.orient;v["box"]=numbers(std::vector<double>{rect.x1,rect.y1,rect.x2,rect.y2});
        std::set<int>tiers;for(auto&[pn,rs]:m.pins)for(auto&r:rs)tiers.insert(r.layer<=10?1:2);v["tiers"]=numbers(std::vector<int>(tiers.begin(),tiers.end()));return v;
    },"Instances");
    // Only compact IDs are retained to preserve the old sorted resource order.
    std::vector<Id> keys;size_t scanned=0;for(auto&[v,r]:resources){if(r.usage||r.fixed)keys.push_back(v);if(++scanned%4096==0)check_time();}
    if(keys.size()>=10000)checkpoint("Resource IDs",keys.size(),keys.size(),"sorting");
    std::sort(keys.begin(),keys.end());check_time();
    array("resources",keys,[&](Id v){auto&r=resources.at(v);return numbers(std::vector<double>{double(v),double(r.capacity),double(r.usage),double(r.fixed),double(std::max(0,r.usage+r.fixed-r.capacity))});},"Resources");
    // Release IDs before allocating the one grid-sized density work vector.
    std::vector<Id>().swap(keys);
    array("heat",heat,number,"Heat");array("thermal_node_counts",thermal_count,number,"Thermal node counts");field("ambient",ambient);
    std::vector<double>density(heat.size(),0);size_t scanned_routes=0;
    for(auto&r:routes){if(r.cross){int x,y,l;decode(r.tsv,x,y,l);density[y*nx+x]++;}if(++scanned_routes%4096==0)check_time();}
    array("tsv_density",density,number,"TSV density");
    // Preserve summation order and the original Pearson/overflow definitions.
    double hm=0,tm=std::accumulate(density.begin(),density.end(),0.0)/density.size();size_t index=0;
    for(double h:heat){hm+=std::max(0.0,h-ambient)/heat.size();if(++index%4096==0)check_time();}
    double numerator=0,hh=0,tt=0;for(size_t i=0;i<heat.size();i++){double a=std::max(0.0,heat[i]-ambient)-hm,b=density[i]-tm;numerator+=a*b;hh+=a*a;tt+=b*b;if(i%4096==0)check_time();}
    Json metrics=Json::object();metrics["nets"]=int(d.nets.size());metrics["routed_nets"]=int(routes.size());metrics["wirelength_dbu_local"]=length;metrics["overflow_local"]=double(overflow());metrics["via_count"]=vias;metrics["tsv_count"]=tsv;metrics["thermal_correlation_local"]=hh>0&&tt>0?Json(numerator/std::sqrt(hh*tt)):Json();metrics["runtime_seconds"]=elapsed();metrics["official_status"]="NOT_EVALUATED";field("metrics",metrics);check_time();
    array("iterations",rounds,[](const Json&r){return r;},"Iterations");field("trace_truncated",trace.truncated);
    Json warnings=Json::array();warnings.push("Local wirelength is graph-edge geometry; local congestion includes approximate rectangular pin/OBS track demand. Neither is an official score.");warnings.push("Grid clipped to DIEAREA. Official evaluator may retain out-of-die GCells; thermal correlation can differ.");warnings.push("First rectangular access shape per terminal is used; full pin-shape access optimization is not implemented.");field("warnings",warnings);
    o<<'}';checkpoint("JSON",1,1,"flushed");
}
void Router::write_result(const std::filesystem::path&p)const{
    auto began=std::chrono::steady_clock::now();double bytes=0;
    write_json(p,[&](std::ostream&o){result_json(o);auto pos=o.tellp();bytes=pos<0?0:double(std::streamoff(pos))+1;});
    if(progress)progress->output("Published",1,1,bytes,std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count(),"result file closed and renamed");
}
void Router::write_guide(const std::filesystem::path&p)const{
    if(p.has_parent_path())std::filesystem::create_directories(p.parent_path());
    auto temp=p;temp+=".tmp";std::ofstream o(temp);if(!o)throw std::runtime_error("Cannot open guide output");
    for(size_t i=0;i<d.nets.size();i++){check_time();validate(d.nets[i],routes[i]);o<<d.nets[i].name<<"\n(\n";for(auto&r:routes[i].guides)o<<std::llround(r.x1)<<' '<<std::llround(r.y1)<<' '<<std::llround(r.x2)<<' '<<std::llround(r.y2)<<' '<<d.layers.at(r.layer).name<<'\n';o<<")\n";}o.close();if(!o)throw std::runtime_error("Guide write failed");if(std::filesystem::exists(p))std::filesystem::remove(p);std::filesystem::rename(temp,p);
}
}

#include "router_v3_impl.hpp"
#include "router_batch_impl.hpp"
