// Physical-rule fixtures; legacy tests run with V3.4 disabled in the same binary.
void v3_4_tests(){
    Trace trace("",16);Config cfg;cfg.v3_stage=3;cfg.v3_batch=cfg.v3_calibrated=cfg.v3_rules_20261029=true;cfg.iterations=0;cfg.margin=100;
    auto rejected=[](auto f){try{f();}catch(const std::exception&){return true;}return false;};
    Design d=tiny();Macro m;m.width=m.height=1;m.pins["A"]={{.1,.35,.9,.36,1},{.1,.35,.9,.36,3}};m.obs={{.1,.35,.9,.36,1}};d.macros["m"]=m;d.instances["u"]={"u","m","N",100,200};
    d.tracks.push_back(d.tracks[1]);Router physical(d,cfg,trace);physical.build_fixed_demand();Id v=physical.id(0,0,1),v3=physical.id(0,0,3);
    require(physical.resource(v).fixed==1&&physical.resource(v3).fixed==1,"No-Track multilayer Pin minimum-one or union incorrect");
    require(physical.capacity(v)==5&&physical.resource(v).capacity==5,"Duplicate Track declarations inflated physical Capacity");
    require(!physical.tsv_legal(physical.id(0,0,10)),"Pin on M1 did not exclude XY TSV site");
    Design zero=d;zero.tracks.clear();Router z(zero,cfg,trace);z.build_fixed_demand();require(z.capacity(v)==0&&z.resource(v).fixed==1,"Zero Capacity was fabricated or Pin minimum-one lost");
    require(RouterTestAccess::v3price(z,z.id(1,0,1))>=1000000,"Zero-Capacity routing penalty absent");
    // Track boundary ownership and duplicate physical geometry union.
    Design boundary=tiny();boundary.macros["m"]=m;boundary.macros["m"].pins["A"]={{0,0,1,1,1}};boundary.macros["m"].obs={{0,0,1,1,1}};boundary.instances=d.instances;
    Router b(boundary,cfg,trace);b.build_fixed_demand();require(b.resource(b.id(0,0,1)).fixed==5,"Pin/OBS track union or half-open boundary incorrect");require(b.resource(b.id(1,0,1)).fixed==0,"Touch-only adjacent GCell charged");
    // Actual placed geometry under every DEF orientation; a nonzero origin.
    for(const auto&o:{"N","S","W","E","FN","FS","FW","FE"}){
        Design rotated=tiny();Macro mm=m;mm.ox=.05;mm.oy=.02;rotated.macros["m"]=mm;rotated.instances["u"]={"u","m",o,1100,1200};Router rr(rotated,cfg,trace);rr.build_fixed_demand();
        auto q=placed_rect(mm.pins.at("A")[0],mm,rotated.instances.at("u"),rotated.dbu);int x=rr.locate(rr.xs,(q.x1+q.x2)/2),y=rr.locate(rr.ys,(q.y1+q.y2)/2);require(rr.resource(rr.id(x,y,1)).fixed>=1&&!rr.tsv_legal(rr.id(x,y,10)),"Oriented Pin geometry missing from physical demand/TSV exclusion");
    }
    Design io=tiny();io.io_pin_rects["PIN.io"]={{120,230,180,250,2},{120,230,180,250,4}};Router ir(io,cfg,trace);ir.build_fixed_demand();require(ir.resource(ir.id(0,0,2)).fixed==1&&ir.resource(ir.id(0,0,4)).fixed==1,"DEF IO multilayer fixed geometry missing");
    {
        auto path=std::filesystem::temp_directory_path()/("v34_io_"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count())+".def");
        struct Cleanup {std::filesystem::path p;~Cleanup(){std::error_code e;std::filesystem::remove(p,e);}} cleanup{path};
        std::ofstream out(path);out<<"VERSION 5.8 ; DESIGN io ; UNITS DISTANCE MICRONS 1000 ; DIEAREA ( 0 0 ) ( 5000 5000 ) ; TRACKS X 0 DO 26 STEP 200 LAYER metal2 ; GCELLGRID X 0 DO 6 STEP 1000 ; GCELLGRID Y 0 DO 6 STEP 1000 ; PINS 1 ; - p + NET n + LAYER metal2 ( 10 20 ) ( 30 40 ) + LAYER metal4 ( 10 20 ) ( 30 40 ) + FIXED ( 2000 2000 ) E ; END PINS NETS 1 ; - n ( PIN p ) ; END NETS END DESIGN\n";out.close();Design parsed=tiny();parse_def(path,parsed);
        const auto&rs=parsed.io_pin_rects.at("PIN.p");require(rs.size()==2&&rs[0].x1==2020&&rs[0].x2==2040&&rs[0].y1==1970&&rs[0].y2==1990&&rs[1].layer==4,"Parsed oriented DEF IO geometry lost");
    }
    auto roomy=[](){Design a=tiny();a.die={100,200,20100,20200,0};a.grid['X'].clear();a.grid['Y'].clear();for(int k=0;k<=20;k++){a.grid['X'].push_back(100+1000*k);a.grid['Y'].push_back(200+1000*k);}a.tracks.clear();for(int l=1;l<=20;l++){a.tracks.push_back({'X',100,200,101,l});a.tracks.push_back({'Y',200,200,101,l});}return a;};
    Design a=roomy();a.nets={{"cross",{{"lo",4600,4700,1},{"hi",14600,14700,12}},true},{"planar",{{"a",600,700,1},{"b",4600,700,1}},false}};
    Router r(a,cfg,trace);r.build_fixed_demand();auto route=r.route(a.nets[0]);r.validate(a.nets[0],route);require(route.tsv!=r.pin_node(a.nets[0].pins[0])&&r.tsv_legal(route.tsv),"Old interface Pin anchor silently reused");
    require(rejected([&]{RouterTestAccess::forced_tsv(r,a.nets[0],r.id(4,4,10));}),"Forced/Hotspot TSV bypassed hard exclusion");
    Net locked{"interface",{{"lo",4600,4700,10},{"hi",4600,4700,11}},true};require(rejected([&]{r.route(locked);}),"Infeasible fixed interface Pins silently waived TSV rule");
    require(route.tsv_demand.size()>2,"5um TSV footprint reduced to two fixed endpoint units");
    RouterTestAccess::quality_cache(r);auto original_quality=RouterTestAccess::batch_quality(r);
    for(int k=0;k<50;k++){r.occupy(route,1);r.occupy(route,-1);}
    r.occupy(route,1);bool allocated=false,armed=false;fail_test_heap_allocations=true;
    try{auto*p=::operator new(16);::operator delete(p);}catch(const std::bad_alloc&){armed=true;}
    try{r.occupy(route,-1);}catch(...){allocated=true;}
    fail_test_heap_allocations=false;require(armed&&!allocated,"Physical rollback allocated memory under heap exhaustion");
    for(const auto&[node,res]:r.resources){(void)node;require(res.usage==0,"Physical TSV transaction leaked usage");}
    require(std::abs(RouterTestAccess::batch_quality(r).at("phi").number()-original_quality.at("phi").number())<1e-7,"Physical TSV dirty cache did not roll back");
    require(rejected([&]{r.occupy(route,-1);}),"Negative Usage not rejected atomically");
    for(const auto&[node,res]:r.resources){(void)node;require(res.usage==0,"Failed usage preflight mutated resources");}
    for(int kind=0;kind<8;kind++){
        auto bad=route;
        if(kind==0)bad.guides[0].x2=bad.guides[0].x1;
        if(kind==1)bad.guides[0].layer=21;
        if(kind==2)bad.guides[0].x1=-1;
        if(kind==3)bad.guides.clear();
        if(kind==4)bad.edges.insert({r.id(19,19,1),*bad.nodes.begin()});
        if(kind==5)bad.steiner_points.push_back(r.id(19,19,1));
        if(kind==6)bad.tsv_demand.clear();
        if(kind==7)bad.edges.clear();
        require(rejected([&]{r.validate(a.nets[0],bad);}),"Guide/branch/edge/TSV invalid candidate accepted");
    }
    Design impossible=roomy();Macro all;all.width=all.height=20;all.obs={{0,0,20,20,1}};impossible.macros["all"]=all;impossible.instances["u"]={"u","all","N",100,200};Router no_site(impossible,cfg,trace);no_site.build_fixed_demand();require(rejected([&]{no_site.route(a.nets[0]);}),"No-legal-TSV design silently chose an illegal GCell");
    // A real multi-Net transaction with physical TSV footprint and exemptions.
    for(size_t i=0;i<a.nets.size();i++){r.routes[i]=r.route(a.nets[i]);r.occupy(r.routes[i],1);}auto saved=r.routes;auto usage=r.resources;
    double wire=0;for(const auto&rr:r.routes)wire+=r.guide_grid_cost(rr);double saved_wire=wire;
    struct Hook:std::stringbuf{bool fired=false;std::function<void()> action;std::streamsize xsputn(const char*s,std::streamsize n)override{if(!fired&&std::string(s,size_t(n)).find("hotspot_net_trial")!=std::string::npos){fired=true;action();}return std::stringbuf::xsputn(s,n);}};
    for(auto order:{std::vector<size_t>{0,1},std::vector<size_t>{1,0}})for(int fault=0;fault<5;fault++){
        Hook hook;hook.action=[&]{if(fault==0)throw std::bad_alloc();if(fault==1)throw std::runtime_error("injected physical transaction fault");if(fault==2)r.cfg.time_limit=-1;if(fault==3)RouterTestAccess::expire_pass(r);if(fault==4)r.cfg.search_limit=1;};trace.out=new std::ofstream;static_cast<std::ostream&>(*trace.out).rdbuf(&hook);trace.out->exceptions(std::ios::badbit|std::ios::failbit);Json detail;
        try{RouterTestAccess::group(r,order,wire,detail);}catch(const std::exception&){}
        trace.out->exceptions(std::ios::goodbit);delete trace.out;trace.out=nullptr;r.cfg=cfg;require(hook.fired,"Physical multi-Net rollback injection missed trial");require(wire==saved_wire,"Rollback changed group wire estimate");
        for(size_t i=0;i<saved.size();i++){r.validate(a.nets[i],r.routes[i]);require(r.route_json(a.nets[i],r.routes[i]).dump()==r.route_json(a.nets[i],saved[i]).dump()&&r.routes[i].demand_exempt==saved[i].demand_exempt&&r.routes[i].tsv_demand==saved[i].tsv_demand&&r.routes[i].resource_demand==saved[i].resource_demand,"Rollback changed physical Route metadata");}
        for(const auto&[node,res]:r.resources)require(res.usage==(usage.count(node)?usage.at(node).usage:0)&&res.usage>=0,"Rollback physical demand mismatch");
    }
    RouterTestAccess::prune(r);for(size_t i=0;i<a.nets.size();i++)r.validate(a.nets[i],r.routes[i]);
    Json thermal=Json::object();thermal["meta"]=Json::object();thermal["meta"]["ambientTemp"]=25;thermal["nodes"]=Json::array();for(int y=0;y<5;y++)for(int x=0;x<5;x++)thermal["nodes"].push(numbers(std::vector<double>{double(x*4),double(y*4),0,double(25+x+y)}));RouterTestAccess::thermal_v3(r,thermal);RouterTestAccess::global_tsv(r);for(size_t i=0;i<a.nets.size();i++)r.validate(a.nets[i],r.routes[i]);
    std::cout<<"V3.4 physical Pin/OBS/IO, multilayer/min-one, zero Capacity, TSV exclusions/footprint, Guide/connectivity, reverse batch rollback, pruning/global TSV passed\n";
}
