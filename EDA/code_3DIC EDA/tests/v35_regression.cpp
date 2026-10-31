#include "router.hpp"
#include <iostream>
using namespace gr;
namespace gr {struct RouterTestAccess {
    static Route local(Router&r,const Net&n,const Route&old){r.alternative_variant_=2;r.v3_batch_active_=true;r.v3_fixed_cache_.clear();return r.route_v35_local(n,old);}
    static double marginal(Router&r,const Route&route){return r.v3_marginal(route);}
};}
void require(bool value,const char*message){if(!value)throw std::runtime_error(message);}
Design fixture(){Design d;d.name="v35_fixture";d.dbu=1000;d.die={0,0,20000,20000,0};d.tsv_cut=true;for(int i=0;i<=20;i++){d.grid['X'].push_back(i*1000);d.grid['Y'].push_back(i*1000);}for(int l=1;l<=20;l++){d.layers[l]={"metal"+std::to_string(l),"ROUTING",l%2?"HORIZONTAL":"VERTICAL",.1,0,.2};d.tracks.push_back({l%2?'Y':'X',50,200,100,l});if(l<20)d.vias.insert({l,l+1});}return d;}
int main(){try{
    Config c;c.iterations=0;Trace trace("",0);auto d=fixture();Net n{"cross",{{"PIN.low",3250,3250,10},{"PIN.high",3250,3250,11}},true};d.io_pin_rects["PIN.low"]={{2000,2000,4500,4500,10}};d.io_pin_rects["PIN.high"]={{2000,2000,4500,4500,11}};d.nets.push_back(n);Router router(d,c,trace);router.build_fixed_demand();auto route=router.route(n);router.validate(n,route);require(route.pin_access_nodes.size()==2,"Missing per-terminal access metadata");require(route.pin_access_nodes[0]%(Id(router.nx)*router.ny)!=route.pin_access_nodes[1]%(Id(router.nx)*router.ny),"Interface accesses overlap");require(router.tsv_legal(route.tsv),"Illegal TSV selected");router.occupy(route,1);auto before=router.overflow();router.occupy(route,-1);for(auto&[v,r]:router.resources){(void)v;require(r.usage==0,"Rollback left resource demand");}router.occupy(route,1);require(router.overflow()==before,"TSV rollback mismatch");
    Route corrupted=route;corrupted.pin_access_nodes[0]=router.id(18,18,10);bool rejected=false;try{router.validate(n,corrupted);}catch(const std::runtime_error&){rejected=true;}require(rejected,"Invalid geometry access not rejected");
    auto high=fixture();Net many;many.name="many";for(int i=0;i<40;i++)many.pins.push_back({"p"+std::to_string(i),i%2?4500.0:500.0,500,1});high.nets.push_back(many);Router hr(high,c,trace);hr.build_fixed_demand();auto old=hr.route(many);hr.resource(hr.id(2,0,1)).fixed=6;auto trial=RouterTestAccess::local(hr,many,old);hr.validate(many,trial);require(trial.pin_access_nodes==old.pin_access_nodes,"Local repair moved terminals");require(trial.nodes!=old.nodes,"Local repair did not generate an alternative");
    Route zero;Id zv=hr.id(10,10,2);zero.nodes.insert(zv);zero.resource_demand={{zv,1}};auto&zr=hr.resource(zv);zr.capacity=0;zr.fixed=1;zr.scoring_ready=false;require(RouterTestAccess::marginal(hr,zero)==0,"Zero-capacity fixed baseline marginal double charged");
    std::cout<<"V3.5 multi-cell interface Pins / strict TSV / geometry access rejection / TSV rollback / 40-Pin local Layer Escape / exact zero-capacity marginal PASS\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
