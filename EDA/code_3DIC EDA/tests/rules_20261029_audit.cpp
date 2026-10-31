// Independent read-only audit of existing result + Guide against real LEF/DEF.
// Links parser.cpp ONLY: does not call Router, its geometry rasterizer, resource
// model, TSV selector, validator, or occupancy implementation.
#include "model.hpp"
#include <algorithm>
#include <array>
#include <fstream>
#include <iostream>
#include <limits>
#include <queue>
#include <tuple>
#include <unordered_set>
using namespace gr;
int main(int argc,char**argv){try{
    if(argc<5)throw std::runtime_error("audit input.lef input.def result.json route.guide [--strict]");
    Design d;parse_lef(std::filesystem::u8path(argv[1]),d);parse_def(std::filesystem::u8path(argv[2]),d);
    if(std::filesystem::file_size(std::filesystem::u8path(argv[3]))>64*1024*1024)throw std::runtime_error("Small-case audit has a 64MiB result safety limit; use a streaming audit for large Cases");
    auto data=Json::parse(read_file(std::filesystem::u8path(argv[3])));std::vector<double> xs,ys;
    for(auto v:data.at("x_lines").arr)xs.push_back(v.number());for(auto v:data.at("y_lines").arr)ys.push_back(v.number());
    int nx=int(xs.size())-1,ny=int(ys.size())-1;Id plane=Id(nx)*ny;
    auto id=[&](int x,int y,int l){return Id(l)*plane+Id(y)*nx+x;};
    auto loc=[](const std::vector<double>&a,double x){return std::clamp(int(std::upper_bound(a.begin(),a.end(),x)-a.begin())-1,0,int(a.size())-2);};
    auto grid=[&](char a,double lo,double hi){std::vector<double> v{lo,hi};for(double x:d.grid.at(a))if(x>lo&&x<hi)v.push_back(x);std::sort(v.begin(),v.end());v.erase(std::unique(v.begin(),v.end()),v.end());return v;};
    if(xs!=grid('X',d.die.x1,d.die.x2)||ys!=grid('Y',d.die.y1,d.die.y2))throw std::runtime_error("Result grid does not match DEF");
    auto each=[&](Rect r,auto visit){
        if(!d.layers.count(r.layer)||r.x2<xs.front()||r.x1>xs.back()||r.y2<ys.front()||r.y1>ys.back())return;
        double x1=std::max(xs.front(),r.x1),x2=std::min(xs.back(),r.x2),y1=std::max(ys.front(),r.y1),y2=std::min(ys.back(),r.y2);
        int xb=x1==x2?loc(xs,x2):std::clamp(int(std::lower_bound(xs.begin(),xs.end(),x2)-xs.begin())-1,0,nx-1);
        int yb=y1==y2?loc(ys,y2):std::clamp(int(std::lower_bound(ys.begin(),ys.end(),y2)-ys.begin())-1,0,ny-1);
        for(int y=loc(ys,y1);y<=yb;y++)for(int x=loc(xs,x1);x<=xb;x++)visit(id(x,y,r.layer),x,y,Rect{std::max(xs[x],x1),std::max(ys[y],y1),std::min(xs[x+1],x2),std::min(ys[y+1],y2),r.layer});
    };
    std::array<std::vector<double>,21> tracks;
    for(int l=1;l<=20;l++)if(d.layers.count(l)){
        std::set<double> values;for(auto t:d.tracks)if(t.layer==l&&t.axis==(d.layers.at(l).direction=="HORIZONTAL"?'Y':'X'))for(int k=0;k<t.count;k++)values.insert(t.start+t.step*k);
        tracks[l].assign(values.begin(),values.end());
    }
    auto in_cell=[&](int l,int x,int y){const auto&t=tracks[l];bool h=d.layers.at(l).direction=="HORIZONTAL";double lo=h?ys[y]:xs[x],hi=h?ys[y+1]:xs[x+1];auto first=std::lower_bound(t.begin(),t.end(),lo),last=(h?y==ny-1:x==nx-1)?std::upper_bound(t.begin(),t.end(),hi):std::lower_bound(t.begin(),t.end(),hi);return std::make_pair(first,last);};
    std::map<Id,std::set<double>> fixed;std::vector<bool> blocked(size_t(plane),false);int minimum_one_shapes=0;
    auto geometry=[&](Rect r,bool pin){each(r,[&](Id v,int x,int y,Rect q){
        blocked[size_t(v%plane)]=true;auto[first,last]=in_cell(r.layer,x,y);bool h=d.layers.at(r.layer).direction=="HORIZONTAL";double lo=h?q.y1:q.x1,hi=h?q.y2:q.x2;bool found=false;
        for(auto it=first;it!=last;it++)if(*it>=lo&&*it<=hi){fixed[v].insert(*it);found=true;}
        if(pin&&!found){++minimum_one_shapes;if(first==last)fixed[v].insert(-std::numeric_limits<double>::infinity());else{auto best=first;for(auto it=first;it!=last;it++)if(std::abs(*it-(lo+hi)/2)<std::abs(*best-(lo+hi)/2))best=it;fixed[v].insert(*best);}}
    });};
    for(const auto&[name,i]:d.instances){(void)name;const auto&m=d.macros.at(i.macro);for(auto r:m.obs)geometry(placed_rect(r,m,i,d.dbu),false);for(const auto&[pn,rs]:m.pins){(void)pn;for(auto r:rs)geometry(placed_rect(r,m,i,d.dbu),true);}}
    for(const auto&[name,rs]:d.io_pin_rects){(void)name;for(auto r:rs)geometry(r,true);}
    auto pin_rects=[&](const Pin&p){auto io=d.io_pin_rects.find(p.name);if(io!=d.io_pin_rects.end())return io->second;auto dot=p.name.rfind('.');if(dot==std::string::npos)throw std::runtime_error("Missing physical terminal identity");const auto&i=d.instances.at(p.name.substr(0,dot));const auto&m=d.macros.at(i.macro);std::vector<Rect> out;for(auto r:m.pins.at(p.name.substr(dot+1)))out.push_back(placed_rect(r,m,i,d.dbu));return out;};
    std::map<std::string,const Net*> expected;for(const auto&n:d.nets)expected[n.name]=&n;
    std::map<Id,std::array<int,3>> recorded;for(const auto&row:data.at("resources").arr)recorded[Id(row.arr.at(0).number())]={int(row.arr.at(1).number()),int(row.arr.at(2).number()),int(row.arr.at(3).number())};
    using Guide=std::tuple<long long,long long,long long,long long,int>;
    std::map<std::string,std::vector<Guide>> guide_file;
    {std::ifstream stream(std::filesystem::u8path(argv[4]));if(!stream)throw std::runtime_error("Cannot open Guide");std::string name,token;while(stream>>name){if(guide_file.count(name))throw std::runtime_error("Duplicate Guide Net: "+name);auto&boxes=guide_file[name];stream>>token;if(token!="(")throw std::runtime_error("Guide syntax: expected (");while(stream>>token&&token!=")"){long long x1=std::stoll(token),y1,x2,y2;std::string l;if(!(stream>>y1>>x2>>y2>>l))throw std::runtime_error("Truncated Guide");boxes.emplace_back(x1,y1,x2,y2,layer_number(l));}if(token!=")")throw std::runtime_error("Unclosed Guide Net");}}
    int conflicts=0,footprint_errors=0,guide_errors=0,connectivity_errors=0,pin_errors=0;Json examples=Json::array(),diagnostics=Json::array();std::map<Id,int> demand;std::set<std::string> seen;
    auto diagnostic=[&](const std::string&net,const std::string&reason){if(diagnostics.arr.size()<30){Json e=Json::object();e["net"]=net;e["reason"]=reason;diagnostics.push(e);}};
    for(const auto&row:data.at("nets").arr){
        auto name=row.at("net").string();if(!expected.count(name)||!seen.insert(name).second)throw std::runtime_error("Unknown/duplicate result Net: "+name);const auto&n=*expected.at(name);
        std::set<Id> nodes;for(auto v:row.at("nodes").arr)nodes.insert(Id(v.number()));bool bad=false;
        for(Id v:nodes){int l=int(v/plane);if(!d.layers.count(l))bad=true;}
        std::map<Id,std::vector<Id>> adjacency;int tsv_edges=0;Id tsv=n.cross?Id(row.at("tsv_node").number()):0;
        for(const auto&e:row.at("edges").arr){Id a=Id(e.arr.at(0).number()),b=Id(e.arr.at(1).number());int x=int(a%nx),y=int(a/nx%ny),l=int(a/plane),xx=int(b%nx),yy=int(b/nx%ny),ll=int(b/plane);
            if(!nodes.count(a)||!nodes.count(b)||!d.layers.count(l)||!d.layers.count(ll)){bad=true;continue;}adjacency[a].push_back(b);adjacency[b].push_back(a);
            if(l==ll){if(std::abs(x-xx)+std::abs(y-yy)!=1||(d.layers.at(l).direction=="HORIZONTAL"?y!=yy:x!=xx))bad=true;}
            else if(std::abs(l-ll)!=1||x!=xx||y!=yy)bad=true;
            else if(std::min(l,ll)==10){++tsv_edges;if(!n.cross||std::min(a,b)!=tsv)bad=true;}
            else if(!d.vias.count({std::min(l,ll),std::max(l,ll)}))bad=true;
        }
        std::set<Id> visited;if(!nodes.empty()){std::vector<Id> todo{*nodes.begin()};while(!todo.empty()){Id v=todo.back();todo.pop_back();if(visited.insert(v).second)for(Id w:adjacency[v])todo.push_back(w);}}
        if(visited!=nodes||nodes.empty())bad=true;
        std::set<Id> own;bool pins_bad=false;
        for(const auto&p:n.pins){if(!nodes.count(id(loc(xs,p.x),loc(ys,p.y),p.layer)))pins_bad=true;for(auto rect:pin_rects(p))each(rect,[&](Id v,int,int,Rect){own.insert(v);});}
        if(row.obj.count("steiner_topology_nodes"))for(auto v:row.at("steiner_topology_nodes").arr)if(!visited.count(Id(v.number())))bad=true;
        if(n.cross){int overlaps=0;for(Id v:nodes)if(v/plane==10&&nodes.count(v+plane))++overlaps;if(tsv_edges!=1||overlaps!=1||tsv/plane!=10)bad=true;}
        else{if(tsv_edges)bad=true;for(Id v:nodes)if((v/plane<=10)!=(n.pins.front().layer<=10))bad=true;}
        if(bad){++connectivity_errors;diagnostic(name,"route/Pins/Via/TSV graph disconnected or illegal");}if(pins_bad){++pin_errors;diagnostic(name,"real DEF/LEF Pin access not connected");}
        std::set<Id> covered;std::vector<Guide> expected_guides;bool bad_guide=false;
        for(const auto&g:row.at("guides").arr){Rect rect{g.arr.at(0).number(),g.arr.at(1).number(),g.arr.at(2).number(),g.arr.at(3).number(),int(g.arr.at(4).number())};expected_guides.emplace_back(std::llround(rect.x1),std::llround(rect.y1),std::llround(rect.x2),std::llround(rect.y2),rect.layer);
            if(!d.layers.count(rect.layer)||rect.x1>=rect.x2||rect.y1>=rect.y2||std::llround(rect.x1)>=std::llround(rect.x2)||std::llround(rect.y1)>=std::llround(rect.y2)||rect.x1<xs.front()||rect.x2>xs.back()||rect.y1<ys.front()||rect.y2>ys.back()){bad_guide=true;std::ostringstream reason;reason<<"invalid Guide Layer "<<rect.layer<<" ["<<rect.x1<<','<<rect.y1<<','<<rect.x2<<','<<rect.y2<<']';diagnostic(name,reason.str());continue;}
            bool overlap=false;each(rect,[&](Id v,int,int,Rect q){if(q.x2>q.x1&&q.y2>q.y1){covered.insert(v);overlap=true;}});if(!overlap)bad_guide=true;
        }
        if(covered!=nodes||!guide_file.count(name)||guide_file.at(name)!=expected_guides)bad_guide=true;
        if(bad_guide){++guide_errors;diagnostic(name,"Guide/GCell coverage or actual Guide file mismatch");}
        std::map<Id,int> net_demand;for(Id v:nodes)if(!own.count(v))net_demand[v]=1;
        if(n.cross){int x=int(tsv%nx),y=int(tsv/nx%ny);double cx=(xs[x]+xs[x+1])/2,cy=(ys[y]+ys[y+1])/2,h=2.5*d.dbu;
            if(blocked[size_t(tsv%plane)]){++conflicts;if(examples.arr.size()<20){Json q=Json::object();q["net"]=name;q["x"]=x;q["y"]=y;examples.push(q);}}
            if(cx-h<xs.front()||cx+h>xs.back()||cy-h<ys.front()||cy+h>ys.back())++footprint_errors;
            for(int l:{10,11})each({cx-h,cy-h,cx+h,cy+h,l},[&](Id v,int xx,int yy,Rect q){if(own.count(v))return;auto[first,last]=in_cell(l,xx,yy);bool horizontal=d.layers.at(l).direction=="HORIZONTAL";int count=0;for(auto it=first;it!=last;it++)count+=*it>=(horizontal?q.y1:q.x1)&&*it<=(horizontal?q.y2:q.x2);net_demand[v]=std::max(net_demand[v],std::max(1,count));});
        }
        for(auto[v,count]:net_demand)demand[v]+=count;
    }
    if(seen.size()!=d.nets.size()||guide_file.size()!=d.nets.size())throw std::runtime_error("Missing/extra Net in result or Guide");
    std::set<Id> all;for(auto&[v,r]:recorded){(void)r;all.insert(v);}for(auto&[v,r]:fixed){(void)r;all.insert(v);}for(auto&[v,r]:demand){(void)r;all.insert(v);}
    int fixed_mismatch=0,capacity_mismatch=0,usage_mismatch=0;long long physical_overflow=0;
    for(Id v:all){int x=int(v%nx),y=int(v/nx%ny),l=int(v/plane);auto[first,last]=in_cell(l,x,y);int cap=int(last-first),f=int(fixed[v].size());auto it=recorded.find(v);int stored_fixed=it==recorded.end()?0:it->second[2],stored_usage=it==recorded.end()?0:it->second[1];fixed_mismatch+=stored_fixed!=f;usage_mismatch+=stored_usage!=demand[v];if(it!=recorded.end())capacity_mismatch+=it->second[0]!=cap;physical_overflow+=std::max(0,f+demand[v]-cap);}
    Json out=Json::object();out["scope"]="independent announcement-derived audit; not official new-evaluator PASS";out["nets"]=int(seen.size());out["tsv_pin_obs_conflict_nets"]=conflicts;out["tsv_footprint_outside_die_nets"]=footprint_errors;out["guide_error_nets"]=guide_errors;out["connectivity_error_nets"]=connectivity_errors;out["unconnected_pin_nets"]=pin_errors;out["fixed_demand_mismatch_cells"]=fixed_mismatch;out["capacity_mismatch_cells"]=capacity_mismatch;out["usage_mismatch_cells"]=usage_mismatch;out["physical_overflow_internal_model"]=double(physical_overflow);out["no_track_pin_cell_shapes"]=minimum_one_shapes;out["blocked_xy_cells"]=int(std::count(blocked.begin(),blocked.end(),true));out["tsv_conflict_examples"]=examples;out["diagnostics"]=diagnostics;
    bool pass=!(conflicts||footprint_errors||guide_errors||connectivity_errors||pin_errors||fixed_mismatch||capacity_mismatch||usage_mismatch);out["static_checks_pass"]=pass;out.write(std::cout);std::cout<<'\n';return argc>5&&std::string(argv[5])=="--strict"&&!pass?1:0;
}catch(const std::exception&e){std::cerr<<"AUDIT ERROR: "<<e.what()<<'\n';return 2;}}
