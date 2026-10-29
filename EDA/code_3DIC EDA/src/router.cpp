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
    if(c.net_order_mode<0||c.net_order_mode>2||c.tree_route_mode<0||c.tree_route_mode>1||c.reroute_priority_mode<0||c.reroute_priority_mode>1)throw std::runtime_error("Invalid V3 strategy mode");
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
void Router::check_time()const{if(progress)progress->poll();if(elapsed()>cfg.time_limit)throw RouterTimeout();}
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
                auto&res=resource(v);double cap=std::max(1,res.capacity),demand=res.usage+res.fixed+1;
                double overload=std::max(0.0,demand-res.capacity)/cap;
                double penalty=cfg.congestion*(overload*overload)+cfg.history*res.history;
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
    Progress::Scope log(progress,"NET_ROUTE",true);
    Route r;r.cross=n.cross;Json sel;
    if(trace.out&&!trace.truncated){sel=Json::object();sel["net"]=n.name;sel["pin_count"]=int(n.pins.size());trace.event("net_selected",sel);}
    std::vector<Id> bottom,top;for(auto&p:n.pins){Id v=pin_node(p);(p.layer<=10?bottom:top).push_back(v);r.nodes.insert(v);}
    if(n.cross){r.tsv=select_tsv(n);bottom.push_back(r.tsv);int x,y,l;decode(r.tsv,x,y,l);Id v=id(x,y,11);top.push_back(v);r.nodes.insert(r.tsv);r.nodes.insert(v);r.edges.insert({r.tsv,v});}
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
    if(!cfg.reroute_priority_mode)return order;
    struct Priority {long long marginal=0;double severity=0;size_t hot=0;};
    std::vector<Priority> keys(routes.size());size_t scanned=0;
    for(size_t i=0;i<routes.size();i++){
        check_time();auto&r=routes[i];int x,y,l;decode(r.tsv,x,y,l);Id upper=id(x,y,11);
        for(Id v:r.nodes){
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
std::pair<long long,int> Router::reroute_pass(int it,long long best){
    check_time();for(auto&[v,res]:resources)if(res.usage&&res.usage+res.fixed>res.capacity)res.history+=double(res.usage+res.fixed-res.capacity)/std::max(1,res.capacity);
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
    auto order=initial_order();
    for(size_t position=0;position<order.size();position++){size_t i=order[position];if(progress)progress->net(position+1,d.nets.size(),d.nets[i].name,0);check_time();try{routes[i]=route(d.nets[i]);occupy(routes[i],1);if(trace.out&&!trace.truncated)trace.event("net_added",route_json(d.nets[i],routes[i]));if(progress)progress->completed();}catch(const RouterTimeout&){throw;}catch(const std::bad_alloc&){throw;}catch(const std::exception&e){throw std::runtime_error("Net "+d.nets[i].name+": "+e.what());}}
    int stagnant=0;long long best;{Progress::Scope overflow_log(progress,"OVERFLOW");best=overflow();}Json initial=Json::object();initial["iteration"]=0;initial["overflow_local"]=double(best);rounds.push_back(initial);trace.event("iteration_done",initial);
    for(int it=1;it<=cfg.iterations&&best>0&&stagnant<cfg.stagnation;it++){
        if(progress)progress->pass(d.nets.size(),it);
        Progress::Scope iteration_log(progress,"OPTIMIZATION");
        auto[now,rerouted]=reroute_pass(it,best);bool accepted=now<best;
        if(!accepted)stagnant++;else{best=now;stagnant=0;}
        Json j=Json::object();j["iteration"]=it;j["candidate_overflow_local"]=double(now);j["overflow_local"]=double(best);j["accepted"]=accepted;j["rerouted_nets"]=rerouted;rounds.push_back(j);trace.event("iteration_done",j);
    }
    Json end=Json::object();end["overflow_local"]=double(overflow());end["runtime_seconds"]=elapsed();trace.event("routing_complete",end);
}
Json Router::route_json(const Net&n,const Route&r)const{
    GR_COUNT(route_json_calls,1);
    Json j=Json::object();j["net"]=n.name;j["cross_die"]=n.cross;j["nodes"]=numbers(std::vector<Id>(r.nodes.begin(),r.nodes.end()));Json edges=Json::array();int vias=0;
    for(auto&e:r.edges){Json v=Json::array();v.push(double(e.a));v.push(double(e.b));edges.push(v);int x,y,l,xx,yy,ll;decode(e.a,x,y,l);decode(e.b,xx,yy,ll);if(l!=ll&&std::min(l,ll)!=10)vias++;}j["edges"]=edges;
    Json gs=Json::array();for(auto&r:r.guides){Json g=numbers(std::vector<double>{r.x1,r.y1,r.x2,r.y2,double(r.layer)});gs.push(g);}j["guides"]=gs;
    Json ps=Json::array();for(auto&p:n.pins){Json v=Json::object();v["name"]=p.name;v["x"]=p.x;v["y"]=p.y;v["layer"]=p.layer;v["node"]=double(pin_node(p));ps.push(v);}j["pins"]=ps;
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
    field("schema_version",1);field("source","independent_cpp_router");field("design",d.name);field("dbu_per_micron",d.dbu);
    if(cfg.net_order_mode||cfg.tree_route_mode||cfg.reroute_priority_mode){Json modes=Json::object();modes["net_order_mode"]=cfg.net_order_mode;modes["tree_route_mode"]=cfg.tree_route_mode;modes["reroute_priority_mode"]=cfg.reroute_priority_mode;modes["routing_strategy"]=cfg.tree_route_mode?"tree_aware":"mst_astar";field("algorithm",modes);}
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
