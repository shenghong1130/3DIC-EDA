#include "router.hpp"
#include <iostream>
using namespace gr;
namespace gr {
struct RouterTestAccess {
    static bool pair(Router&r,std::array<size_t,2>nets,std::array<Id,2>sites,
                     double&wire,Json&detail) {
        return r.v44_try_tsv_pair(nets,sites,wire,1e10,nullptr,detail);
    }
    static void phase(Router&r,double&wire) {
        r.v44_global_tsv_pairs(wire,1e10,nullptr);
    }
    static const Json&stats(const Router&r){return r.v44_stats_;}
};
}
int main(){
    try {
        Design d;d.dbu=1000;d.name="v44_empty_fixture";
        d.die={0,0,9000,9000,0};
        for(int i=0;i<=3;i++){d.grid['X'].push_back(i*3000);d.grid['Y'].push_back(i*3000);}
        for(int layer=1;layer<=20;layer++){
            d.layers[layer]={"metal"+std::to_string(layer),"ROUTING",
                             layer%2?"HORIZONTAL":"VERTICAL",.1,0,3};
            d.tracks.push_back({layer%2?'Y':'X',50,3000,3,layer});
            if(layer<20)d.vias.insert({layer,layer+1});
        }
        Trace trace("",0);Config config;config.time_limit=120;
        Router router(d,config,trace);
        double wire=0;Json result;
        bool accepted=RouterTestAccess::pair(router,{0,1},{0,1},wire,result);
        if(accepted||result.at("reason").string()!="invalid_pair")
            throw std::runtime_error("Invalid pair was not rejected");
        RouterTestAccess::phase(router,wire);
        if(RouterTestAccess::stats(router).at("accepted").number()!=0)
            throw std::runtime_error("Empty design accepted a TSV move");
        if(wire!=0)throw std::runtime_error("Empty design changed wirelength");
        std::cout<<"V4.4 empty-design and invalid-pair smoke checks PASS\n";
        return 0;
    }catch(const std::exception&e){
        std::cerr<<e.what()<<'\n';return 1;
    }
}
