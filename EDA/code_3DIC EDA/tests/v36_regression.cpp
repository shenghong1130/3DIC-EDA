#include "router.hpp"
#include <iostream>
using namespace gr;
namespace gr {struct RouterTestAccess {
    static void optimize(Router&r){r.redistribute_tsv_global();}
    static Route forced(Router&r,const Net&n,Id v){r.v3_forced_tsv_=v;auto route=r.route(n);r.v3_forced_tsv_=0;return route;}
    static bool move(Router&r,size_t i,Id site,double&wire,double limit,Json&detail){return r.v36_try_tsv(i,site,wire,limit,nullptr,0,detail);}
    static double cost(Router&r){double sum=0;for(auto&route:r.routes)sum+=r.guide_grid_cost(route);return sum;}
    static double phi(Router&r){return r.batch_quality_full().phi;}
    static const Json&stats(Router&r){return r.v3_global_tsv_stats_;}
    static void inject(Router&r,bool v){r.v36_abort_after_install_=v;}
    static bool cold_guard(Router&r,Id site,double&wire,Json&detail){
        r.v3_thermal_xs_=r.xs;r.v3_thermal_ys_=r.ys;r.v3_thermal_rise_.assign(size_t(r.nx)*r.ny,0);
        size_t source=r.v3_thermal_cell(r.routes[0].tsv);r.v3_thermal_rise_[source]=100;
        std::vector<int>bins(r.v3_thermal_rise_.size(),0);bins[source]=int(r.routes.size());
        DensityCorrelation density(r.v3_thermal_rise_,std::move(bins));
        return r.v36_try_tsv(0,site,wire,1e100,&density,density.value(),detail);
    }
};}
void require(bool b,const char*m){if(!b)throw std::runtime_error(m);}
Design fixture(int nets){
    Design d;d.name="dense_tsv_fixture";d.version="5.8";d.dbu=1000;d.die={0,0,90000,90000,0};d.tsv_cut=true;
    for(int k=0;k<=30;k++){d.grid['X'].push_back(k*3000);d.grid['Y'].push_back(k*3000);}
    for(int l=1;l<=20;l++){d.layers[l]={"metal"+std::to_string(l),"ROUTING",l%2?"HORIZONTAL":"VERTICAL",.1,0,.2};d.tracks.push_back({l%2?'Y':'X',50,200,450,l});if(l<20)d.vias.insert({l,l+1});}
    for(int k=0;k<nets;k++){
        std::string low="PIN.L"+std::to_string(k),high="PIN.H"+std::to_string(k);
        d.io_pin_rects[low]={{6500,6500,6600,6600,1}};d.io_pin_rects[high]={{66500,66500,66600,66600,11}};
        d.nets.push_back({"n"+std::to_string(k),{{low,6550,6550,1},{high,66550,66550,11}},true});
    }return d;
}
std::map<Id,int> usage(const Router&r){std::map<Id,int>s;for(auto[v,p]:r.resources)if(p.usage)s[v]=p.usage;return s;}
int main(){try{
    auto d=fixture(32);Config c;c.iterations=0;c.time_limit=180;Trace trace("",0);Router r(d,c,trace);r.build_fixed_demand();
    for(size_t i=0;i<d.nets.size();i++){r.routes[i]=RouterTestAccess::forced(r,d.nets[i],r.id(12,12,10));r.occupy(r.routes[i],1);}
    const auto before=usage(r);const auto old_nodes=r.routes[0].nodes;const auto over=r.overflow();
    const auto initial_routes=r.routes; // Small fixture oracle only, never production state.
    double wire=RouterTestAccess::cost(r),original_wire=wire;Json detail;
    require(!RouterTestAccess::move(r,0,r.id(2,2,10),wire,1e100,detail),"Pin-blocked TSV candidate accepted");
    require(!RouterTestAccess::move(r,0,r.id(0,0,10),wire,1e100,detail),"Outside-footprint boundary candidate accepted");
    require(usage(r)==before,"Illegal candidate changed resources");
    require(!RouterTestAccess::cold_guard(r,r.id(10,10,10),wire,detail)&&detail.at("reason").string()=="thermal_guard","Thermal guard accepted a cold redistribution");
    require(usage(r)==before,"Thermal rejection changed occupancy");
    require(!RouterTestAccess::move(r,0,r.id(10,10,10),wire,0,detail),"Wire guard failed");
    require(usage(r)==before&&r.routes[0].nodes==old_nodes&&wire==original_wire,"Rejected reroute was not restored");
    RouterTestAccess::inject(r,true);bool interrupted=false;
    for(auto[x,y]:std::vector<std::pair<int,int>>{{10,10},{14,14},{8,12},{12,8}}){
        try{RouterTestAccess::move(r,0,r.id(x,y,10),wire,1e100,detail);}catch(const std::runtime_error&e){if(std::string(e.what()).find("Injected")==0){interrupted=true;break;}throw;}
    }
    RouterTestAccess::inject(r,false);require(interrupted,"Fault injection never reached a viable installed candidate");
    require(usage(r)==before&&r.routes[0].nodes==old_nodes&&r.routes[0].tsv==r.id(12,12,10)&&wire==original_wire,"Interrupted TSV install did not roll back");
    r.validate(d.nets[0],r.routes[0]);
    const double phi=RouterTestAccess::phi(r);RouterTestAccess::optimize(r);
    auto s=RouterTestAccess::stats(r);require(s.at("moved").number()>0,"Dense fixture made no TSV moves");
    require(r.overflow()<over&&RouterTestAccess::phi(r)<=phi+1e-7,"Full-resource congestion did not improve");
    require(s.at("max_tsv_per_gcell_after").number()<32,"TSVs were not redistributed");
    require(s.at("attempted").number()<=8192&&s.at("eligible_ranked_nets").number()<=2048,"Large-case work budget violated");
    for(size_t i=0;i<r.routes.size();i++)r.validate(d.nets[i],r.routes[i]);
    Router repeat(d,c,trace);repeat.build_fixed_demand();repeat.routes=initial_routes;
    for(auto&route:repeat.routes)repeat.occupy(route,1);RouterTestAccess::optimize(repeat);
    for(size_t i=0;i<r.routes.size();i++)require(repeat.routes[i].tsv==r.routes[i].tsv&&repeat.routes[i].nodes==r.routes[i].nodes,"Deterministic dense run changed routes");
    for(auto&route:r.routes)r.occupy(route,-1);for(auto[v,p]:r.resources)require(p.usage==0,"Incremental footprint double charge/negative usage");
    for(auto it=r.routes.rbegin();it!=r.routes.rend();++it)r.occupy(*it,1);for(auto[v,p]:r.resources)require(p.usage>=0,"Nonnegative usage broken");
    std::vector<double> h{1,2,5,9};DensityCorrelation rho(h,{8,0,0,0});double predicted=rho.after_move(0,3);rho.move(0,3);DensityCorrelation full(h,{7,0,0,1});require(std::abs(predicted-full.value())<1e-12&&std::abs(rho.value()-full.value())<1e-12,"Incremental thermal correlation differs from full calculation");
    // Full case005-sized thermal work array and TSV population, without
    // routing a large Case or allocating a quadratic TSV-pair matrix.
    std::vector<double> large_heat(1856134);std::vector<int> large_counts(1856134,0);
    for(size_t i=0;i<large_heat.size();i++)large_heat[i]=double(i%997);
    for(size_t i=0;i<39542;i++)++large_counts[i*31%large_counts.size()];
    DensityCorrelation large(large_heat,std::move(large_counts));require(std::isfinite(large.after_move(0,999)),"Large thermal delta failed");
    std::cout<<"V3.6 TSV legality / footprint / duplicate demand / reverse restore / interruption rollback / full connectivity / dense congestion improvement / thermal delta / explicit scale budgets PASS\n"<<s.dump()<<'\n';return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
