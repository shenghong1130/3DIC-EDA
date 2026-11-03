#include "router.hpp"
#include <iostream>
#include "allocation_guard.hpp"
using namespace gr;
namespace gr {struct RouterTestAccess {
    static void optimize(Router&r){r.optimize_v4();}
    static void escape(Router&r,double&wire){r.v4_layer_escape(wire,1e100);}
    static Route forced(Router&r,const Net&n,Id v){r.v3_forced_tsv_=v;auto out=r.route(n);r.v3_forced_tsv_=0;return out;}
    static void prepared(Router&r,const Net&n,Route&route){r.make_guides(route);r.prepare_v34_route(n,route);r.validate(n,route);}
    static bool trial(Router&r,size_t i,Id v,double&w,double limit,Json&detail,DensityCorrelation*d=nullptr,bool thermal=false){return r.v4_try(i,v,thermal,w,limit,d,d?d->value():0,0,0,detail);}
    static const Json&stats(Router&r){return r.v4_stats_;}
    static Json attribution(Router&r){return r.v4_attribution();}
    static void inject(Router&r,bool v){r.v4_inject_install_=v;}
#ifdef GR_V4_TEST
    static void heap_fault(Router&r){r.v4_install_hook_=[](){fail_test_heap_allocations=true;throw std::bad_alloc();};}
    static void clear_fault(Router&r){r.v4_install_hook_={};}
#endif
    static void search_budget(Router&r,size_t n){r.v4_search_limit_=n;}
    static void thermal(Router&r){
        r.v3_thermal_xs_=r.xs;r.v3_thermal_ys_=r.ys;r.v3_thermal_rise_.resize(size_t(r.nx)*r.ny);r.v3_thermal_ready_=true;
        for(int y=0;y<r.ny;y++)for(int x=0;x<r.nx;x++)r.v3_thermal_rise_[size_t(y)*r.nx+x]=x+y;
        std::vector<int> bins(r.v3_thermal_rise_.size());for(auto&t:r.routes)if(t.cross)++bins[r.v3_thermal_cell(t.tsv)];
        DensityCorrelation density(r.v3_thermal_rise_,std::move(bins));double before=density.value(),wire=cost(r);
        r.v4_tsv_refine(true,wire,1e100,&density);if(density.value()+1e-12<before)throw std::runtime_error("Thermal stage regressed Pearson");
        std::vector<int> after(r.v3_thermal_rise_.size());for(auto&t:r.routes)if(t.cross)++after[r.v3_thermal_cell(t.tsv)];DensityCorrelation oracle(r.v3_thermal_rise_,std::move(after));
        if(std::abs(oracle.value()-density.value())>1e-12)throw std::runtime_error("Thermal stage density out of sync with routes");
    }
    static double cost(Router&r){double w=0;for(auto&t:r.routes)w+=r.guide_grid_cost(t);return w;}
};}
void require(bool v,const char*s){if(!v)throw std::runtime_error(s);}
Design fixture(int count,bool cross){
    Design d;d.dbu=1000;d.version="5.8";d.name="v4_oracle";d.die={0,0,90000,90000,0};d.tsv_cut=true;
    for(int i=0;i<=30;i++){d.grid['X'].push_back(i*3000);d.grid['Y'].push_back(i*3000);}
    for(int l=1;l<=20;l++){d.layers[l]={"metal"+std::to_string(l),"ROUTING",l%2?"HORIZONTAL":"VERTICAL",.1,0,.2};d.tracks.push_back({l%2?'Y':'X',50,200,450,l});if(l<20)d.vias.insert({l,l+1});}
    for(int i=0;i<count;i++){
        Net n;n.name="n"+std::to_string(i);n.cross=cross;
        for(int k=0;k<(cross?2:48);k++){std::string name="PIN_"+std::to_string(i)+"_"+std::to_string(k);bool top=k%2;double x=top?66550:6550,y=cross?(top?66550:6550):31550;int l=cross&&top?11:1;
            d.io_pin_rects[name]={{x-50,y-50,x+50,y+50,l}};n.pins.push_back({name,x,y,l});}
        d.nets.push_back(n);
    }return d;
}
std::map<Id,int> usage(Router&r){std::map<Id,int>u;for(auto[v,s]:r.resources)if(s.usage)u[v]=s.usage;return u;}
int main(){try{
    Trace trace("",0);Config cfg;cfg.time_limit=180;cfg.iterations=0;
    auto inner=fixture(32,false);Router m(inner,cfg,trace);m.build_fixed_demand();
    for(size_t i=0;i<inner.nets.size();i++){
        Route r;for(int x=2;x<=22;x++){r.nodes.insert(m.id(x,10,1));if(x>2)r.edges.insert({m.id(x-1,10,1),m.id(x,10,1)});}
        for(auto&p:inner.nets[i].pins)r.pin_access_nodes.push_back(m.pin_node(p));RouterTestAccess::prepared(m,inner.nets[i],r);m.routes[i]=std::move(r);m.occupy(m.routes[i],1);
    }
    auto mandatory=m.routes[0].pin_access_nodes;auto original=usage(m);double w=RouterTestAccess::cost(m);Json detail;
    require(!RouterTestAccess::trial(m,0,m.id(10,10,1),w,0,detail),"Wire guard accepted forbidden growth");require(usage(m)==original,"Wire rejection corrupted occupancy");
    RouterTestAccess::inject(m,true);bool fault=false;
    try{RouterTestAccess::trial(m,0,m.id(10,10,1),w,1e100,detail);}catch(const std::runtime_error&e){fault=std::string(e.what()).find("Injected V4")==0;}
    RouterTestAccess::inject(m,false);require(fault,"Install interruption did not exercise a viable layer escape");require(usage(m)==original&&m.routes[0].pin_access_nodes==mandatory,"Layer escape interruption failed rollback");
#ifdef GR_V4_TEST
    RouterTestAccess::heap_fault(m);bool allocation=false;
    try{RouterTestAccess::trial(m,0,m.id(10,10,1),w,1e100,detail);}catch(const std::bad_alloc&){fail_test_heap_allocations=false;allocation=true;}
    fail_test_heap_allocations=false;RouterTestAccess::clear_fault(m);require(allocation&&usage(m)==original,"Heap exhaustion rollback allocated or leaked usage");
#endif
    RouterTestAccess::search_budget(m,1);require(!RouterTestAccess::trial(m,0,m.id(10,10,1),w,1e100,detail)&&usage(m)==original,"Expansion budget did not restore route");RouterTestAccess::search_budget(m,8192);
    m.cfg.time_limit=0;bool timed=false;try{RouterTestAccess::trial(m,0,m.id(10,10,1),w,1e100,detail);}catch(const RouterTimeout&){timed=true;}m.cfg.time_limit=180;
    require(timed&&usage(m)==original,"Global timeout was swallowed or failed rollback");
    auto before=RouterTestAccess::attribution(m);RouterTestAccess::escape(m,w);auto after=RouterTestAccess::attribution(m);
    require(after.at("total_overflow").number()<before.at("total_overflow").number(),"M1 escape did not reduce real incremental overflow");
    auto stats=RouterTestAccess::stats(m).at("layer_escape");require(stats.at("high_pin_searched").number()>0&&stats.at("high_pin_accepted").number()>0,"High-Pin local patch not exercised");
    for(size_t i=0;i<m.routes.size();i++){m.validate(inner.nets[i],m.routes[i]);require(m.routes[i].pin_access_nodes==mandatory,"Mandatory M1 access was removed");}
    // case005 M1 is VERTICAL: transpose the complete fixture, including real
    // Pin rectangles, Tracks and preferred directions, rather than assume M1 H.
    {
        auto rotated=fixture(32,false);for(auto&[l,layer]:rotated.layers)layer.direction=l%2?"VERTICAL":"HORIZONTAL";
        for(auto&t:rotated.tracks)t.axis=t.axis=='X'?'Y':'X';
        for(auto&n:rotated.nets)for(auto&p:n.pins)std::swap(p.x,p.y);
        for(auto&[name,rects]:rotated.io_pin_rects){(void)name;for(auto&q:rects){std::swap(q.x1,q.y1);std::swap(q.x2,q.y2);}}
        Router vertical(rotated,cfg,trace);vertical.build_fixed_demand();
        for(size_t i=0;i<rotated.nets.size();i++){Route route;for(int y=2;y<=22;y++){route.nodes.insert(vertical.id(10,y,1));if(y>2)route.edges.insert({vertical.id(10,y-1,1),vertical.id(10,y,1)});}for(auto&p:rotated.nets[i].pins)route.pin_access_nodes.push_back(vertical.pin_node(p));RouterTestAccess::prepared(vertical,rotated.nets[i],route);vertical.routes[i]=std::move(route);vertical.occupy(vertical.routes[i],1);}
        auto before_vertical=RouterTestAccess::attribution(vertical);double vw=RouterTestAccess::cost(vertical);RouterTestAccess::escape(vertical,vw);
        require(RouterTestAccess::attribution(vertical).at("total_overflow").number()<before_vertical.at("total_overflow").number(),"VERTICAL M1 layer escape failed");
    }
    // Fixed demand is distinct from movable demand, including zero Capacity.
    Id baseline=m.id(25,25,1);m.resource(baseline).fixed=20;m.resource(baseline).capacity=15;
    require(RouterTestAccess::attribution(m).at("fixed_baseline_overflow").number()==5,"Fixed baseline attribution incorrect");
    m.resource(baseline).capacity=0;require(RouterTestAccess::attribution(m).at("zero_capacity_demand_cells").number()==1,"Zero capacity demand lost");
    Route invalid=m.routes[0];invalid.edges.insert({m.id(2,10,1),m.id(2,10,3)});bool rejected=false;try{m.validate(inner.nets[0],invalid);}catch(const std::runtime_error&){rejected=true;}require(rejected,"Nonadjacent Via accepted");
    auto cross=fixture(32,true);Router r(cross,cfg,trace);r.build_fixed_demand();
    for(size_t i=0;i<r.routes.size();i++){r.routes[i]=RouterTestAccess::forced(r,cross.nets[i],r.id(12,12,10));r.occupy(r.routes[i],1);}
    const auto saved=r.routes;auto u=usage(r);double wire=RouterTestAccess::cost(r);
    require(!RouterTestAccess::trial(r,0,r.id(2,2,10),wire,1e100,detail),"Pin-blocked TSV accepted");require(usage(r)==u,"Blocked TSV changed demand");
    RouterTestAccess::inject(r,true);fault=false;
    for(auto[x,y]:std::vector<std::pair<int,int>>{{10,10},{14,14},{8,12},{12,8}}){try{RouterTestAccess::trial(r,0,r.id(x,y,10),wire,1e100,detail);}catch(const std::runtime_error&e){if(std::string(e.what()).find("Injected V4")==0){fault=true;break;}throw;}}
    RouterTestAccess::inject(r,false);require(fault&&usage(r)==u&&r.routes[0].tsv==r.id(12,12,10),"TSV install failed atomic rollback");
    RouterTestAccess::optimize(r);auto out=RouterTestAccess::stats(r);require(out.at("global_tsv").at("moved").number()>0,"Regional TSV scheduling made no dense-fixture move");
    for(size_t i=0;i<r.routes.size();i++)r.validate(cross.nets[i],r.routes[i]);
    Router repeat(cross,cfg,trace);repeat.build_fixed_demand();repeat.routes=saved;for(auto&t:repeat.routes)repeat.occupy(t,1);RouterTestAccess::optimize(repeat);
    for(size_t i=0;i<r.routes.size();i++)require(r.routes[i].nodes==repeat.routes[i].nodes&&r.routes[i].tsv==repeat.routes[i].tsv,"Deterministic work fixture differs");
    RouterTestAccess::thermal(r);for(size_t i=0;i<r.routes.size();i++)r.validate(cross.nets[i],r.routes[i]);
    require(RouterTestAccess::stats(r).at("thermal_refinement").at("moved").number()>0,"Thermal refinement made no warm legal move");
    for(auto it=r.routes.rbegin();it!=r.routes.rend();++it)r.occupy(*it,-1);require(usage(r).empty(),"Reverse order rollback leaked/shared TSV usage");
    std::vector<double>heat{1,2,5,9};DensityCorrelation rho(heat,{8,0,0,0});double predicted=rho.after_move(0,3);rho.move(0,3);DensityCorrelation oracle(heat,{7,0,0,1});require(std::abs(predicted-oracle.value())<1e-12,"Incremental thermal Pearson mismatch");
    std::cout<<"V4 fixed/routing baseline, mandatory M1 Pin, M2/M3 local escape, high-Pin patch, illegal Via, wire/via guards, install interruption, TSV legality/unique overlap, reverse occupancy restore, density oracle, regional scheduling and deterministic fixture PASS\n"<<out.dump()<<'\n';return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
