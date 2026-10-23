#include "router.hpp"
#include <algorithm>
#include <queue>
#include <unordered_set>
#include <numeric>
#include <fstream>
#include <iostream>
namespace gr {
Router::Router(Design&design,Config c,Trace&t):d(design),cfg(c),trace(t),started(std::chrono::steady_clock::now()){
    auto lines=[&](char a,double lo,double hi){std::vector<double> v{lo,hi};for(double x:d.grid.at(a))if(x>lo&&x<hi)v.push_back(x);std::sort(v.begin(),v.end());v.erase(std::unique(v.begin(),v.end()),v.end());return v;};
    xs=lines('X',d.die.x1,d.die.x2);ys=lines('Y',d.die.y1,d.die.y2);nx=int(xs.size())-1;ny=int(ys.size())-1;
    if(nx<1||ny<1||Id(nx)*ny>10000000)throw std::runtime_error("Unsupported GCell size (>10M cells)");
    unit=std::min(xs[1]-xs[0],ys[1]-ys[0]);heat.assign(size_t(nx)*ny,ambient);thermal_count.assign(heat.size(),0);routes.resize(d.nets.size());
    Json j=Json::object();j["cols"]=nx;j["rows"]=ny;j["layer_count"]=int(d.layers.size());trace.event("graph_built",j);
}
int Router::locate(const std::vector<double>&a,double x)const{if(x<a.front()||x>a.back())throw std::runtime_error("Coordinate outside grid");return std::min(int(a.size())-2,int(std::upper_bound(a.begin(),a.end(),x)-a.begin())-1);}
Id Router::pin_node(const Pin&p)const{return id(locate(xs,p.x),locate(ys,p.y),p.layer);}
Rect Router::cell(Id v)const{int x,y,l;decode(v,x,y,l);return {xs.at(x),ys.at(y),xs.at(x+1),ys.at(y+1),l};}
std::pair<double,double> Router::center(Id v)const{auto r=cell(v);return {(r.x1+r.x2)/2,(r.y1+r.y2)/2};}
double Router::elapsed()const{return std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();}
void Router::check_time()const{if(elapsed()>cfg.time_limit)throw std::runtime_error("Router time budget exceeded");}
static int count_tracks(const Track&t,double lo,double hi,bool last){double e=1e-8;int begin=std::max(0,int(std::ceil((lo-t.start)/t.step-e)));int end=std::min(t.count,int(std::ceil((hi-t.start)/t.step-(last?-e:e))));return std::max(0,end-begin);}
int Router::capacity(Id v)const{auto r=cell(v);auto&l=d.layers.at(r.layer);char axis=l.direction=="HORIZONTAL"?'Y':'X';int c=0;for(auto&t:d.tracks)if(t.layer==r.layer&&t.axis==axis)c+=count_tracks(t,axis=='X'?r.x1:r.y1,axis=='X'?r.x2:r.y2,axis=='X'?r.x2==xs.back():r.y2==ys.back());return c;}
Resource& Router::resource(Id v){auto [it,newentry]=resources.try_emplace(v);if(newentry)it->second.capacity=capacity(v);return it->second;}
void Router::load_thermal(const std::filesystem::path&p){
    auto j=Json::parse(read_file(p));ambient=j.at("meta").at("ambientTemp").number();auto& nodes=j.at("nodes");if(nodes.type!=Json::Array||nodes.arr.empty())throw std::runtime_error("Thermal nodes must be a nonempty array");
    heat.assign(heat.size(),0);std::map<std::pair<double,double>,std::pair<double,int>> projected;
    for(auto&n:nodes.arr){if(n.type!=Json::Array||n.arr.size()!=4)throw std::runtime_error("Thermal node must be [x_um,y_um,z,temp]");double x=n.arr[0].number(),y=n.arr[1].number();n.arr[2].number();double temp=n.arr[3].number();auto&v=projected[{x,y}];v.first+=temp;v.second++;}
    for(auto&[xy,avg]:projected){double x=d.die.x1+xy.first*d.dbu,y=d.die.y1+xy.second*d.dbu;if(x<xs.front()||x>xs.back()||y<ys.front()||y>ys.back())continue;int k=locate(ys,y)*nx+locate(xs,x);heat[k]+=avg.first/avg.second;thermal_count[k]++;}
    int known=0;for(size_t k=0;k<heat.size();k++)if(thermal_count[k]){heat[k]/=thermal_count[k];known++;}if(!known)throw std::runtime_error("No thermal nodes mapped to DIEAREA");
    // Deterministic nearest valid value in previous column/row, then reverse fill at leading gaps.
    for(int y=0;y<ny;y++)for(int x=0;x<nx;x++){int k=y*nx+x;if(!thermal_count[k]){if(x&&std::isfinite(heat[k-1]))heat[k]=heat[k-1];else if(y)heat[k]=heat[k-nx];else heat[k]=std::numeric_limits<double>::quiet_NaN();}}
    double next=ambient;for(size_t k=heat.size();k-->0;){if(std::isfinite(heat[k]))next=heat[k];else heat[k]=next;}
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
    if(start==goal)return {start};
    int ax,ay,al,bx,by,bl;decode(start,ax,ay,al);decode(goal,bx,by,bl);
    auto [gx,gy]=center(goal);auto heuristic=[&](Id v){auto [x,y]=center(v);int ix,iy,l;decode(v,ix,iy,l);return cfg.wire*(std::abs(x-gx)+std::abs(y-gy))/unit+cfg.via*std::abs(l-bl);};
    struct State{double f,g;Id v;bool operator<(const State&o)const{return f!=o.f?f>o.f:v>o.v;}};
    struct Info{double g;Id parent;};
    for(int margin=cfg.margin;;margin=std::min(std::max(nx,ny),margin*2)){
        check_time();int xl=std::max(0,std::min(ax,bx)-margin),xh=std::min(nx-1,std::max(ax,bx)+margin),yl=std::max(0,std::min(ay,by)-margin),yh=std::min(ny-1,std::max(ay,by)+margin);
        std::priority_queue<State> q;std::unordered_map<Id,Info> dist;dist.reserve(4096);dist[start]={0,start};q.push({heuristic(start),0,start});size_t expanded=0;
        while(!q.empty()){
            auto cur=q.top();q.pop();if(cur.g>dist.at(cur.v).g+1e-10)continue;
            if(cur.v==goal){std::vector<Id> result;for(Id v=goal;;v=dist.at(v).parent){result.push_back(v);if(v==start)break;}std::reverse(result.begin(),result.end());return result;}
            if(++expanded>cfg.search_limit)throw std::runtime_error("A* expansion limit; increase search_limit");
            if(expanded%4096==0)check_time();
            int x,y,l;decode(cur.v,x,y,l);
            auto visit=[&](int xx,int yy,int ll){
                if(xx<xl||xx>xh||yy<yl||yy>yh||ll<lo||ll>hi||!d.layers.count(ll))return;
                Id v=id(xx,yy,ll);bool planar=l==ll;
                if(!planar&&!d.vias.count({std::min(l,ll),std::max(l,ll)}))return;
                if(planar&&cross&&(l==10||l==11))return; // Only access cells on interface layers.
                auto&res=resource(v);double cap=std::max(1,res.capacity),demand=res.usage+res.fixed+1;
                double penalty=cfg.congestion*std::pow(std::max(0.0,demand-res.capacity)/cap,2)+cfg.history*res.history;
                auto[cx,cy]=center(cur.v);auto[xxc,yyc]=center(v);double base=planar?cfg.wire*(std::abs(cx-xxc)+std::abs(cy-yyc))/unit:cfg.via;
                double cost=cur.g+base+penalty;auto it=dist.find(v);if(it==dist.end()||cost+1e-10<it->second.g){dist[v]={cost,cur.v};q.push({cost+heuristic(v),cost,v});}
            };
            if(d.layers.at(l).direction=="HORIZONTAL"){visit(x-1,y,l);visit(x+1,y,l);}else{visit(x,y-1,l);visit(x,y+1,l);}visit(x,y,l-1);visit(x,y,l+1);
        }
        if(xl==0&&yl==0&&xh==nx-1&&yh==ny-1)break;
    }
    throw std::runtime_error("No legal path between GCells "+std::to_string(start)+" and "+std::to_string(goal));
}
std::vector<std::pair<int,int>> Router::mst(const std::vector<Id>&ps)const{
    int n=int(ps.size());std::vector<double> best(n,1e100);std::vector<int> parent(n,-1);std::vector<bool> seen(n,false);std::vector<std::pair<int,int>> tree;if(n==0)return tree;best[0]=0;
    for(int k=0;k<n;k++){int u=-1;for(int v=0;v<n;v++)if(!seen[v]&&(u==-1||best[v]<best[u]))u=v;seen[u]=true;if(parent[u]>=0)tree.emplace_back(parent[u],u);auto[a,b]=center(ps[u]);int x,y,l;decode(ps[u],x,y,l);
        for(int v=0;v<n;v++)if(!seen[v]){auto[c,e]=center(ps[v]);int xx,yy,ll;decode(ps[v],xx,yy,ll);double w=cfg.wire*(std::abs(a-c)+std::abs(b-e))/unit+cfg.via*std::abs(l-ll);if(w<best[v]){best[v]=w;parent[v]=u;}}
    }return tree;
}
Id Router::select_tsv(const Net&n){
    if(!d.layers.count(10)||!d.layers.count(11)||!d.tsv_cut)throw std::runtime_error("Cross-die net requires M10/M11 and TSV CUT definition");
    std::set<std::pair<int,int>> low,up;std::vector<std::pair<int,int>> pins;int xl=nx-1,xh=0,yl=ny-1,yh=0;
    for(auto&p:n.pins){int x=locate(xs,p.x),y=locate(ys,p.y);pins.emplace_back(x,y);xl=std::min(xl,x);xh=std::max(xh,x);yl=std::min(yl,y);yh=std::max(yh,y);if(p.layer==10)low.insert({x,y});if(p.layer==11)up.insert({x,y});}
    std::vector<std::pair<int,int>> shared;std::set_intersection(low.begin(),low.end(),up.begin(),up.end(),std::back_inserter(shared));
    if(shared.size()>1)throw std::runtime_error("Multiple fixed TSV pin GCells require explicit disambiguation: "+n.name);
    if(!shared.empty())return id(shared[0].first,shared[0].second,10);
    double tmin=*std::min_element(heat.begin(),heat.end()),tmax=*std::max_element(heat.begin(),heat.end()),score=1e100;Id answer=0;
    int stride=std::max(1,int(std::ceil(std::sqrt(double((xh-xl+1)*(yh-yl+1))/4096))));
    std::set<std::pair<int,int>> candidates(pins.begin(),pins.end());for(int y=yl;y<=yh;y+=stride)for(int x=xl;x<=xh;x+=stride)candidates.insert({x,y});
    for(auto[x,y]:candidates){double w=0;auto[cx,cy]=center(id(x,y,10));for(auto&p:n.pins)w+=(std::abs(p.x-cx)+std::abs(p.y-cy))/unit;w*=cfg.wire/std::max(size_t(1),n.pins.size());
        auto&a=resource(id(x,y,10));auto&b=resource(id(x,y,11));w+=cfg.tsv+cfg.congestion*(std::max(0,a.usage+a.fixed+2-a.capacity)+std::max(0,b.usage+b.fixed+2-b.capacity));
        // Nonnegative penalty for cold sites; positive heat/TSV correlation is desired.
        w+=cfg.thermal*(tmax>tmin?(tmax-heat[y*nx+x])/(tmax-tmin):0);
        if(w<score){score=w;answer=id(x,y,10);}
    }return answer;
}
Route Router::route(const Net&n){
    Route r;r.cross=n.cross;Json sel=Json::object();sel["net"]=n.name;sel["pin_count"]=int(n.pins.size());trace.event("net_selected",sel);
    std::vector<Id> bottom,top;for(auto&p:n.pins){Id v=pin_node(p);(p.layer<=10?bottom:top).push_back(v);r.nodes.insert(v);}
    if(n.cross){r.tsv=select_tsv(n);bottom.push_back(r.tsv);int x,y,l;decode(r.tsv,x,y,l);Id v=id(x,y,11);top.push_back(v);r.nodes.insert(r.tsv);r.nodes.insert(v);r.edges.insert({r.tsv,v});}
    auto connect=[&](std::vector<Id>&ps,int lo,int hi){std::sort(ps.begin(),ps.end());ps.erase(std::unique(ps.begin(),ps.end()),ps.end());auto tree=mst(ps);Json topo=sel;topo["tier"]=lo==1?"bottom":"top";topo["terminal_nodes"]=numbers(ps);Json ts=Json::array();for(auto[a,b]:tree){Json e=Json::array();e.push(double(ps[a]));e.push(double(ps[b]));ts.push(e);}topo["mst_edges"]=ts;trace.event("topology",topo);
        for(auto[a,b]:tree){check_time();Json ev=sel;ev["from"]=double(ps[a]);ev["to"]=double(ps[b]);trace.event("search_started",ev);auto p=search(ps[a],ps[b],lo,hi,n.cross);ev["nodes"]=numbers(p);trace.event("path_found",ev);for(Id v:p)r.nodes.insert(v);for(size_t i=1;i<p.size();i++)r.edges.insert({std::min(p[i-1],p[i]),std::max(p[i-1],p[i])});}
    };if(!bottom.empty())connect(bottom,1,10);if(!top.empty())connect(top,11,20);
    make_guides(r);validate(n,r);return r;
}
void Router::occupy(const Route&r,int sign){for(Id v:r.nodes){auto&res=resource(v);res.usage+=sign;if(res.usage<0)throw std::runtime_error("Negative resource usage");}if(r.cross){int x,y,l;decode(r.tsv,x,y,l);resource(r.tsv).usage+=sign;resource(id(x,y,11)).usage+=sign;}}
long long Router::overflow()const{long long total=0;for(auto&[v,r]:resources)total+=std::max(0,r.fixed+r.usage-r.capacity);return total;}
double Router::wirelength(const Route&r)const{double w=0;for(auto&e:r.edges){int ax,ay,al,bx,by,bl;decode(e.a,ax,ay,al);decode(e.b,bx,by,bl);if(al==bl){auto[x,y]=center(e.a);auto[u,v]=center(e.b);w+=std::abs(x-u)+std::abs(y-v);}}return w;}
void Router::make_guides(Route&r)const{
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
void Router::run(){
    for(size_t i=0;i<d.nets.size();i++){check_time();try{routes[i]=route(d.nets[i]);occupy(routes[i],1);trace.event("net_added",route_json(d.nets[i],routes[i]));}catch(const std::exception&e){throw std::runtime_error("Net "+d.nets[i].name+": "+e.what());}}
    int stagnant=0;long long best=overflow();Json initial=Json::object();initial["iteration"]=0;initial["overflow_local"]=double(best);rounds.push_back(initial);trace.event("iteration_done",initial);
    for(int it=1;it<=cfg.iterations&&best>0&&stagnant<cfg.stagnation;it++){
        check_time();for(auto&[v,res]:resources)if(res.usage&&res.usage+res.fixed>res.capacity)res.history+=double(res.usage+res.fixed-res.capacity)/std::max(1,res.capacity);
        auto saved=routes;int rerouted=0;
        for(size_t i=0;i<routes.size();i++){bool hot=false;for(Id v:routes[i].nodes){auto&res=resource(v);if(res.usage+res.fixed>res.capacity){hot=true;break;}}if(!hot)continue;
            auto old=routes[i];occupy(old,-1);Json rem=Json::object();rem["net"]=d.nets[i].name;rem["iteration"]=it;trace.event("net_removed",rem);
            try{routes[i]=route(d.nets[i]);}catch(...){occupy(old,1);routes[i]=old;trace.event("net_added",route_json(d.nets[i],old));throw;}
            occupy(routes[i],1);trace.event("net_added",route_json(d.nets[i],routes[i]));rerouted++;
        }
        long long now=overflow();bool accepted=now<best;
        if(!accepted){for(size_t i=0;i<routes.size();i++){occupy(routes[i],-1);Json rem=Json::object();rem["net"]=d.nets[i].name;trace.event("net_removed",rem);routes[i]=std::move(saved[i]);occupy(routes[i],1);trace.event("net_added",route_json(d.nets[i],routes[i]));}stagnant++;}else{best=now;stagnant=0;}
        Json j=Json::object();j["iteration"]=it;j["candidate_overflow_local"]=double(now);j["overflow_local"]=double(best);j["accepted"]=accepted;j["rerouted_nets"]=rerouted;rounds.push_back(j);trace.event("iteration_done",j);
    }
    Json end=Json::object();end["overflow_local"]=double(overflow());end["runtime_seconds"]=elapsed();trace.event("routing_complete",end);
}
Json Router::route_json(const Net&n,const Route&r)const{
    Json j=Json::object();j["net"]=n.name;j["cross_die"]=n.cross;j["nodes"]=numbers(std::vector<Id>(r.nodes.begin(),r.nodes.end()));Json edges=Json::array();int vias=0;
    for(auto&e:r.edges){Json v=Json::array();v.push(double(e.a));v.push(double(e.b));edges.push(v);int x,y,l,xx,yy,ll;decode(e.a,x,y,l);decode(e.b,xx,yy,ll);if(l!=ll&&std::min(l,ll)!=10)vias++;}j["edges"]=edges;
    Json gs=Json::array();for(auto&r:r.guides){Json g=numbers(std::vector<double>{r.x1,r.y1,r.x2,r.y2,double(r.layer)});gs.push(g);}j["guides"]=gs;
    Json ps=Json::array();for(auto&p:n.pins){Json v=Json::object();v["name"]=p.name;v["x"]=p.x;v["y"]=p.y;v["layer"]=p.layer;v["node"]=double(pin_node(p));ps.push(v);}j["pins"]=ps;
    j["wirelength_dbu_local"]=wirelength(r);j["via_count"]=vias;j["tsv_count"]=n.cross?1:0;j["tsv_node"]=n.cross?Json(double(r.tsv)):Json();j["status"]="ROUTED";return j;
}
Json Router::result_json()const{
    Json j=Json::object();j["schema_version"]=1;j["source"]="independent_cpp_router";j["design"]=d.name;j["dbu_per_micron"]=d.dbu;j["die"]=numbers(std::vector<double>{d.die.x1,d.die.y1,d.die.x2,d.die.y2});j["x_lines"]=numbers(xs);j["y_lines"]=numbers(ys);
    Json ls=Json::array();for(auto&[n,l]:d.layers){Json v=Json::object();v["number"]=n;v["name"]=l.name;v["tier"]=n<=10?"bottom":"top";v["direction"]=l.direction;ls.push(v);}j["layers"]=ls;
    Json ns=Json::array();double length=0;int tsv=0,vias=0;for(size_t i=0;i<routes.size();i++){auto v=route_json(d.nets[i],routes[i]);length+=v.at("wirelength_dbu_local").number();tsv+=routes[i].cross;vias+=int(v.at("via_count").number());ns.push(v);}j["nets"]=ns;
    Json ins=Json::array();for(auto&[name,i]:d.instances){auto&m=d.macros.at(i.macro);auto rect=placed_rect({0,0,m.width,m.height,0},m,i,d.dbu);Json v=Json::object();v["name"]=name;v["macro"]=i.macro;v["orientation"]=i.orient;v["box"]=numbers(std::vector<double>{rect.x1,rect.y1,rect.x2,rect.y2});std::set<int>tiers;for(auto&[pn,rs]:m.pins)for(auto&r:rs)tiers.insert(r.layer<=10?1:2);v["tiers"]=numbers(std::vector<int>(tiers.begin(),tiers.end()));ins.push(v);}j["instances"]=ins;
    Json rs=Json::array();std::vector<Id> keys;for(auto&[v,r]:resources)if(r.usage||r.fixed)keys.push_back(v);std::sort(keys.begin(),keys.end());for(Id v:keys){auto&r=resources.at(v);rs.push(numbers(std::vector<double>{double(v),double(r.capacity),double(r.usage),double(r.fixed),double(std::max(0,r.usage+r.fixed-r.capacity))}));}j["resources"]=rs;
    j["heat"]=numbers(heat);j["thermal_node_counts"]=numbers(thermal_count);j["ambient"]=ambient;
    std::vector<double>density(heat.size(),0);for(auto&r:routes)if(r.cross){int x,y,l;decode(r.tsv,x,y,l);density[y*nx+x]++;}j["tsv_density"]=numbers(density);
    double hm=0,tm=std::accumulate(density.begin(),density.end(),0.0)/density.size();for(double h:heat)hm+=std::max(0.0,h-ambient)/heat.size();double numerator=0,hh=0,tt=0;for(size_t i=0;i<heat.size();i++){double a=std::max(0.0,heat[i]-ambient)-hm,b=density[i]-tm;numerator+=a*b;hh+=a*a;tt+=b*b;}
    Json metrics=Json::object();metrics["nets"]=int(d.nets.size());metrics["routed_nets"]=int(routes.size());metrics["wirelength_dbu_local"]=length;metrics["overflow_local"]=double(overflow());metrics["via_count"]=vias;metrics["tsv_count"]=tsv;metrics["thermal_correlation_local"]=hh>0&&tt>0?Json(numerator/std::sqrt(hh*tt)):Json();metrics["runtime_seconds"]=elapsed();metrics["official_status"]="NOT_EVALUATED";j["metrics"]=metrics;
    Json rr=Json::array();for(auto&r:rounds)rr.push(r);j["iterations"]=rr;j["trace_truncated"]=trace.truncated;
    Json warnings=Json::array();warnings.push("Local wirelength is graph-edge geometry; local congestion includes approximate rectangular pin/OBS track demand. Neither is an official score.");warnings.push("Grid clipped to DIEAREA. Official evaluator may retain out-of-die GCells; thermal correlation can differ.");warnings.push("First rectangular access shape per terminal is used; full pin-shape access optimization is not implemented.");j["warnings"]=warnings;return j;
}
void Router::write_guide(const std::filesystem::path&p)const{
    if(p.has_parent_path())std::filesystem::create_directories(p.parent_path());
    auto temp=p;temp+=".tmp";std::ofstream o(temp);if(!o)throw std::runtime_error("Cannot open guide output");
    for(size_t i=0;i<d.nets.size();i++){validate(d.nets[i],routes[i]);o<<d.nets[i].name<<"\n(\n";for(auto&r:routes[i].guides)o<<std::llround(r.x1)<<' '<<std::llround(r.y1)<<' '<<std::llround(r.x2)<<' '<<std::llround(r.y2)<<' '<<d.layers.at(r.layer).name<<'\n';o<<")\n";}o.close();if(!o)throw std::runtime_error("Guide write failed");if(std::filesystem::exists(p))std::filesystem::remove(p);std::filesystem::rename(temp,p);
}
}
