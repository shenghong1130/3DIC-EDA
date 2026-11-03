#include "router.hpp"
#include "physical_resources_20261103.hpp"
#include <iostream>
using namespace gr;
void require(bool x,const char* why){if(!x)throw std::runtime_error(why);}
int main(){try{
    std::vector<double> tracks{2,8};
    require(no_track_pin_index(tracks.begin(),tracks.end(),5,PinTrackPolicy::NearestInCell)==0,"nearest tie assumption changed");
    require(no_track_pin_index(tracks.begin(),tracks.end(),6,PinTrackPolicy::NearestInCell)==1,"nearest is not unconditional lower Track");
    require(no_track_pin_index(tracks.begin(),tracks.end(),6,PinTrackPolicy::LowerInCell)==0,"lower diagnostic");
    require(no_track_pin_index(tracks.begin(),tracks.end(),4,PinTrackPolicy::UpperInCell)==1,"upper diagnostic");
    Design d;d.dbu=1;d.die={0,0,20,20,0};d.grid['X']={0,10,20};d.grid['Y']={0,10,20};
    for(int l=1;l<=20;l++){d.layers[l]={"metal"+std::to_string(l),"ROUTING",l%2?"HORIZONTAL":"VERTICAL",.1,0,.2};d.tracks.push_back({l%2?'Y':'X',2,6,3,l});if(l<20)d.vias.insert({l,l+1});}
    d.tsv_cut=true;
    // Actual geometries on two layers, duplicate IO Pins, OBS, half-open boundary.
    d.io_pin_rects["p"]={{1,5.5,2,6.5,1},{1,5.5,2,6.5,3}};
    d.io_pin_rects["duplicate"]={{1,5.5,2,6.5,1}};
    Macro macro;macro.width=20;macro.height=20;macro.obs={{1,7.5,2,8.5,1}};
    d.macros["m"]=macro;d.instances["i"]={"i","m","N",0,0};
    auto demand=[&](PinTrackPolicy policy){PhysicalModel20261103 model(d,policy);std::map<Id,int> out;model.build([&](Id v,int,int n){out[v]=n;});return out;};
    auto nearest=demand(PinTrackPolicy::NearestInCell),lower=demand(PinTrackPolicy::LowerInCell),upper=demand(PinTrackPolicy::UpperInCell);
    PhysicalGrid grid(d);require(nearest[grid.id(0,0,1)]==1,"Pin/OBS same Track not unioned");
    require(lower[grid.id(0,0,1)]==2&&upper[grid.id(0,0,1)]==1,"diagnostic assignment must expose union uncertainty");
    require(nearest[grid.id(0,0,3)]==1,"multilayer IO geometry omitted");
    d.io_pin_rects["boundary"]={{10,10,11,11,2}};auto bounded=demand(PinTrackPolicy::NearestInCell);
    require(bounded[grid.id(1,1,2)]==1&&!bounded.count(grid.id(0,1,2)),"left GCell forced assignment");
    // Fixed Pin at zero Capacity stays visible; an actual route is rejected.
    d.tracks.erase(std::remove_if(d.tracks.begin(),d.tracks.end(),[](auto&t){return t.layer==1;}),d.tracks.end());
    PhysicalModel20261103 zero(d);int fixed=0;zero.build([&](Id v,int cap,int n){if(v==grid.id(0,0,1)){require(cap==0,"capacity fabricated");fixed=n;}});require(fixed>=1,"zero-capacity fixed Pin hidden");
    Trace trace("",0);Config cfg;cfg.time_limit=30;Router router(d,cfg,trace);router.build_fixed_demand();
    bool rejected=false;try{router.search(router.id(0,0,1),router.id(1,0,1),1,10,false);}catch(const std::runtime_error&){rejected=true;}
    require(rejected,"actual zero-capacity routing accepted");
    // Large rectangle count is handled by the bounded stripe workspace.
    for(int i=0;i<5000;i++)d.io_pin_rects["bulk"+std::to_string(i)]={{12,12,13,13,4}};
    PhysicalModel20261103 bulk(d);bulk.build([](Id,int,int){},[](){},64);
    require(bulk.max_workspace_bytes<=64&&bulk.rectangles>=5000,"geometry workspace unbounded");
    std::cout<<"20261103 assignment alternatives, no-Track/zero-capacity, union, multilayer IO, GCell ownership and bounded geometry PASS\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
