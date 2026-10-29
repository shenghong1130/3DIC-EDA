// Controlled performance fixtures, not official routing scores or large Cases.
#include "router.hpp"
#include <iostream>
using namespace gr;
static Design grid(int size){
    Design d;d.name="controlled_benchmark";d.version="5.8";d.dbu=1000;d.die={0,0,double(size*100),double(size*100),0};d.tsv_cut=true;
    for(int i=0;i<=size;i++){d.grid['X'].push_back(i*100.0);d.grid['Y'].push_back(i*100.0);}
    for(int l=1;l<=20;l++){d.layers[l]={"metal"+std::to_string(l),"ROUTING",l%2?"HORIZONTAL":"VERTICAL",.01,.01,.01};d.tracks.push_back({'X',0,.01,size*10000+1,l});d.tracks.push_back({'Y',0,.01,size*10000+1,l});if(l<20&&l!=10)d.vias.insert({l,l+1});}
    return d;
}
int main(int argc,char**argv){try{
    if(argc!=3)throw std::runtime_error("benchmark astar|tsv|reroute|tree_legacy|tree_aware|tree_combined output_prefix");
    std::string mode=argv[1],prefix=argv[2];auto began=std::chrono::steady_clock::now();
    Design d=grid(mode=="tsv"?256:96);Config c;c.time_limit=90;c.iterations=mode=="reroute"?2:0;
    bool tree_fixture=mode=="tree_legacy"||mode=="tree_aware"||mode=="tree_combined";
    if(tree_fixture){c.tree_route_mode=mode=="tree_legacy"?0:1;c.net_order_mode=mode=="tree_combined"?2:0;c.reroute_priority_mode=mode=="tree_combined"?1:0;}
    int count=mode=="astar"?300:mode=="tsv"?512:mode=="reroute"?1800:tree_fixture?300:0;
    if(!count)throw std::runtime_error("Unknown benchmark mode");
    for(int i=0;i<count;i++){
        int x=mode=="tsv"?32+i%180:16+i%60,y=mode=="tsv"?32+(i*7)%180:16+(i*7)%60;
        int dx=mode=="astar"&&i%2?18:6,dy=mode=="astar"&&i%2?15:3;
        d.nets.push_back({"n"+std::to_string(i),{{"a",x*100.0+50,y*100.0+50,1},{"b",(x+dx)*100.0+50,(y+dy)*100.0+50,mode=="tsv"?12:2}},mode=="tsv"});
        if(tree_fixture){auto&net=d.nets.back();net.pins.push_back({"c",(x+2)*100.0+50,(y+9)*100.0+50,1});net.pins.push_back({"d",(x+14)*100.0+50,(y+5)*100.0+50,2});net.pins.push_back({"e",(x+8)*100.0+50,(y+12)*100.0+50,1});}
    }
    if(mode=="reroute")d.nets[0]={"n0",{{"a",50,50,1},{"b",650,350,2}},false};
    Trace t("",32);Router r(d,c,t);
    if(mode=="reroute"){auto&res=r.resource(r.id(0,0,1));res.fixed=res.capacity+1;}
    for(size_t i=0;i<r.heat.size();i++)r.heat[i]=25+double(i%997)/997;
    r.run();double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();
    r.write_result(std::filesystem::u8path(prefix+".result.json"));r.write_guide(std::filesystem::u8path(prefix+".guide"));
    Json j=Json::object();j["fixture"]=mode;j["routing_seconds"]=seconds;j["nets"]=count;j["overflow_local"]=double(r.overflow());std::cout<<j.dump()<<'\n';return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
