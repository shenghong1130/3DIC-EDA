// Independent small-graph checks for the new, explicitly selected architecture.
double multi_edge_cost(Router&r,Id a,Id b,const std::unordered_set<Id>&targets){
    int x,y,l,xx,yy,ll;r.decode(a,x,y,l);r.decode(b,xx,yy,ll);auto[c,d]=r.center(a);auto[e,f]=r.center(b);
    double base=l==ll?r.cfg.wire*(std::abs(c-e)+std::abs(d-f))/r.unit:r.cfg.via;
    if(targets.count(b))return base;
    const auto&s=r.resource(b);double cap=std::max(1,s.capacity),u=double(s.usage)+s.fixed;
    double penalty=r.cfg.congestion*std::pow(std::max(0.0,u+1-s.capacity)/cap,2)+r.cfg.history*s.history;
    if(r.cfg.v3_stage>=2){double before=std::max(0.0,u-s.capacity)/cap,after=std::max(0.0,u+1-s.capacity)/cap;penalty=r.cfg.congestion*(after*after-before*before+.05*std::pow(std::min(1.0,(u+1)/cap),4))+r.cfg.history*s.history;}
    return base+penalty;
}
void v3_global_tests(){
    auto full=[](const std::vector<double>&heat,const std::vector<int>&density){double a=0,b=0;for(double v:heat)a+=v;for(int v:density)b+=v;a/=heat.size();b/=density.size();double xy=0,xx=0,yy=0;for(size_t i=0;i<heat.size();i++){xy+=(heat[i]-a)*(density[i]-b);xx+=(heat[i]-a)*(heat[i]-a);yy+=(density[i]-b)*(density[i]-b);}return xx&&yy?xy/std::sqrt(xx*yy):0;};
    std::vector<double> heat{1,2,3,4};DensityCorrelation joint(heat,{0,0,2,0});
    require(std::abs(joint.after_move(2,3)-full(heat,{0,0,1,1}))<1e-12,"Incremental TSV objective differs from full Pearson");
    double before=joint.value();joint.move(2,3);require(joint.value()>before,"Joint density fixture did not improve");joint.move(3,2);require(std::abs(joint.value()-before)<1e-12,"TSV density undo changed correlation");
    Trace trace("",1);Design d=tiny();d.nets={{"a",{{"lo",4600,4700,1},{"hi",4600,4700,12}},true},{"b",{{"lo",4600,4700,1},{"hi",4600,4700,12}},true},{"fixed",{{"lo",600,700,10},{"hi",600,700,11}},true}};
    Config c;c.v3_stage=3;c.iterations=0;c.margin=100;Router router(d,c,trace);
    Json input=Json::object(),nodes=Json::array();for(int y=0;y<5;y++)for(int x=0;x<5;x++){Json point=Json::array();point.push(x+.5);point.push(y+.5);point.push(0);point.push(25+2*x+y);nodes.push(point);}input["nodes"]=nodes;
    RouterTestAccess::thermal_v3(router,input);
    Id anchor=router.id(0,0,10);for(size_t i=0;i<d.nets.size();i++){router.routes[i]=RouterTestAccess::forced_tsv(router,d.nets[i],anchor);router.occupy(router.routes[i],1);}
    auto original=router.routes;RouterTestAccess::global_tsv(router);int moved=0;
    for(size_t i=0;i<d.nets.size();i++){router.validate(d.nets[i],router.routes[i]);if(router.routes[i].tsv!=anchor)++moved;}
    require(moved>0&&router.routes[2].tsv==anchor,"Global TSV did not move a real route or moved a fixed interface");
    for(const auto&[v,res]:router.resources){int expected=0;for(const auto&r:router.routes){expected+=int(r.nodes.count(v));int x,y,l;router.decode(r.tsv,x,y,l);expected+=int(v==r.tsv||v==router.id(x,y,11));}require(res.usage==expected,"Global TSV usage/extra interface demand mismatch");}
    for(const auto&r:router.routes)router.occupy(r,-1);
    router.routes=original;
    for(const auto&r:router.routes)router.occupy(r,1);
    auto resources=router.resources;
    struct Fault:std::stringbuf{bool fired=false;std::streamsize xsputn(const char*s,std::streamsize n)override{if(std::string(s,size_t(n)).find("global_tsv_moved")!=std::string::npos){fired=true;throw std::runtime_error("injected TSV commit error");}return std::stringbuf::xsputn(s,n);}} buffer;
    trace.out=new std::ofstream;static_cast<std::ostream&>(*trace.out).rdbuf(&buffer);trace.out->exceptions(std::ios::badbit|std::ios::failbit);
    bool failed=false;try{RouterTestAccess::global_tsv(router);}catch(const std::exception&){failed=true;}
    trace.out->exceptions(std::ios::goodbit);delete trace.out;trace.out=nullptr;require(failed&&buffer.fired,"Global TSV failure injection missed a committed move");
    for(size_t i=0;i<original.size();i++)require(router.route_json(d.nets[i],router.routes[i]).dump()==router.route_json(d.nets[i],original[i]).dump(),"Global TSV exception did not restore complete old route");
    for(const auto&[v,res]:router.resources){auto found=resources.find(v);require(res.usage==(found==resources.end()?0:found->second.usage),"Global TSV exception did not restore Usage");}
    std::cout<<"Global TSV fixture: "<<moved<<" physical route(s) moved; fixed TSV and failure rollback verified\n";
    Design extended=tiny();Router boundary(extended,c,trace);extended.grid['X'].push_back(6100);RouterTestAccess::thermal_v3(boundary,input);require(RouterTestAccess::thermal_size(boundary)==30,"Thermal projection clipped raw out-of-die grid");
    extended.grid['X'].clear();extended.grid['Y'].clear();for(int k=0;k<=1001;k++){extended.grid['X'].push_back(k*1000);extended.grid['Y'].push_back(k*1000);}RouterTestAccess::thermal_v3(boundary,input);require(!RouterTestAccess::thermal_ready(boundary),"Global thermal memory guard ignored");
}
double multi_oracle(Router&r,Id start,const std::unordered_set<Id>&targets,int lo,int hi){
    using Entry=std::pair<double,Id>;std::priority_queue<Entry,std::vector<Entry>,std::greater<Entry>> queue;
    std::map<Id,double> dist;queue.push({0,start});dist[start]=0;
    while(!queue.empty()){
        auto[c,v]=queue.top();queue.pop();if(c!=dist.at(v))continue;if(targets.count(v))return c;
        int x,y,l;r.decode(v,x,y,l);
        auto visit=[&](int xx,int yy,int ll){
            if(xx<0||xx>=r.nx||yy<0||yy>=r.ny||ll<lo||ll>hi||!r.d.layers.count(ll))return;
            if(l!=ll&&!r.d.vias.count({std::min(l,ll),std::max(l,ll)}))return;
            Id next=r.id(xx,yy,ll);double cost=c+multi_edge_cost(r,v,next,targets);auto found=dist.find(next);
            if(found==dist.end()||cost+1e-10<found->second){dist[next]=cost;queue.push({cost,next});}
        };
        if(r.d.layers.at(l).direction=="HORIZONTAL"){visit(x-1,y,l);visit(x+1,y,l);}else{visit(x,y-1,l);visit(x,y+1,l);}visit(x,y,l-1);visit(x,y,l+1);
    }return 1e100;
}
void v3_generation_tests(){
    L1TargetIndex index({{0,0,1},{10,10,3},{5,5,2}});
    for(int x=0;x<=10;x++)for(int y=0;y<=10;y++){std::array<double,3> p{double(x),double(y),2};double expected=1e100;for(auto q:std::vector<std::array<double,3>>{{0,0,1},{10,10,3},{5,5,2}}){double d=0;for(int k=0;k<3;k++)d+=std::abs(p[k]-q[k]);expected=std::min(expected,d);}require(index.distance(p)==expected,"KD nearest-point bound differs from linear oracle");}
    require(L1TargetIndex({{0,0,0},{10,10,0}}).distance({0,10,0})==10,"Multi-target heuristic collapsed to whole-tree bbox");
    Trace trace("",1);Config c;c.iterations=0;c.v3_stage=1;c.margin=100;
    Design d=tiny();d.grid['X']={100,400,1600,3500,5100};d.grid['Y']={200,900,2700,5200};Router r(d,c,trace);
    r.resource(r.id(0,1,1)).usage=9;r.resource(r.id(2,1,2)).fixed=12;r.resource(r.id(1,0,2)).history=2;
    std::unordered_set<Id> targets{r.id(0,2,1),r.id(3,0,2)};
    for(int stage:{1,2}){
        r.cfg.v3_stage=stage;RouterTestAccess::phase(r,stage>=2);
        for(int y=0;y<r.ny;y++)for(int x=0;x<r.nx;x++){
            Id start=r.id(x,y,1);auto path=r.search_multi(start,targets,1,2,false);double cost=0;for(size_t k=1;k<path.size();k++)cost+=multi_edge_cost(r,path[k-1],path[k],targets);
            require(std::abs(cost-multi_oracle(r,start,targets,1,2))<1e-9,"New multi-target A* differs from independent Dijkstra");
        }
    }
    d.vias.clear();bool failed=false;try{r.search_multi(r.id(0,0,1),{r.id(3,0,2)},1,2,false);}catch(const std::runtime_error&){failed=true;}require(failed,"New search accepted illegal Via");
    failed=false;try{r.search_multi(r.id(0,0,1),{},1,2,false);}catch(const std::exception&){failed=true;}require(failed,"New search accepted empty target");
    Design example=tiny();example.die={0,0,11000,11000,0};example.grid['X'].clear();example.grid['Y'].clear();
    for(int i=0;i<=11;i++){example.grid['X'].push_back(i*1000);example.grid['Y'].push_back(i*1000);}
    example.layers[1].direction="VERTICAL";example.layers[2].direction="HORIZONTAL";
    Net three{"three",{{"a",500,500,1},{"b",10500,500,1},{"c",5500,10500,1}},false};example.nets={three};
    Config old=c;old.v3_stage=0;old.congestion=0;old.history=1;Config next=old;next.v3_stage=1;
    Router baseline(example,old,trace),steiner(example,next,trace);
    // The lower corridor remains best for A-B, but reusing it is charged again
    // by independent A-C search. The new tree can attach without paying twice.
    baseline.resource(baseline.id(2,0,2)).history=1.5;steiner.resource(steiner.id(2,0,2)).history=1.5;
    auto topology=RouterTestAccess::topology(steiner,{steiner.id(0,0,1),steiner.id(10,0,1),steiner.id(5,10,1)},1,10);
    require(topology.at("points").arr.size()>=1&&topology.at("cost").number()==20,"Hanan fixture did not create length-20 Steiner topology");
    Route a=baseline.route(three),b=steiner.route(three);steiner.validate(three,b);
    require(steiner.wirelength(b)<baseline.wirelength(a),"New topology did not improve actual legal route");
    std::cout<<"Steiner fixture: actual wirelength "<<baseline.wirelength(a)<<" -> "<<steiner.wirelength(b)<<" DBU\n";
    Config production=next;production.v3_stage=3;Router selected(example,production,trace);selected.resource(selected.id(2,0,2)).history=1.5;auto accepted=selected.route(three);
    std::cout<<"Joint guard fixture Guide costs: legacy "<<baseline.guide_grid_cost(a)<<", proposed "<<steiner.guide_grid_cost(b)<<", selected "<<selected.guide_grid_cost(accepted)<<"\n";
    require(selected.guide_grid_cost(accepted)<=baseline.guide_grid_cost(a),"Production joint guard accepted a Guide wirelength regression");
    if(accepted.steiner_points.empty())require(selected.route_json(three,accepted).dump()==baseline.route_json(three,a).dump(),"Rejected Steiner candidate did not retain complete reference route");
    // With ample capacity the geometric saving is a joint improvement, so the
    // production path must retain the real mapped branch point, not just a label.
    Router ample(example,production,trace);ample.resource(ample.id(2,0,2)).history=1.5;
    for(const auto&[layer,description]:example.layers){(void)description;for(int y=0;y<ample.ny;y++)for(int x=0;x<ample.nx;x++)ample.resource(ample.id(x,y,layer)).capacity=100;}
    auto improved=ample.route(three);ample.validate(three,improved);
    require(!improved.steiner_points.empty()&&ample.guide_grid_cost(improved)<baseline.guide_grid_cost(a),"Production joint guard did not retain a proven Steiner improvement");
    struct Budget:std::stringbuf{Router&r;bool fired=false;explicit Budget(Router&v):r(v){}std::streamsize xsputn(const char*s,std::streamsize n)override{if(!fired&&std::string(s,size_t(n)).find("bounded_hanan_steiner")!=std::string::npos){fired=true;RouterTestAccess::expire_v3(r);}return std::stringbuf::xsputn(s,n);}} budget(steiner);
    trace.out=new std::ofstream;static_cast<std::ostream&>(*trace.out).rdbuf(&budget);trace.out->exceptions(std::ios::badbit|std::ios::failbit);
    Route fallback=steiner.route(three);trace.out->exceptions(std::ios::goodbit);delete trace.out;trace.out=nullptr;
    require(budget.fired&&steiner.route_json(three,fallback).dump()==baseline.route_json(three,a).dump(),"Steiner soft timeout did not restore legal MST fallback");
    for(int count:{2,3,4,12,17}){
        Design fixture=tiny();Net net;net.name="pins";for(int k=0;k<count;k++)net.pins.push_back({std::to_string(k),600.0+(k%5)*1000,700.0+((k/5)%5)*1000,1});fixture.nets={net};Router candidate(fixture,c,trace);candidate.run();candidate.validate(net,candidate.routes[0]);
    }
    Design crossing=tiny();Net cross{"cross",{{"a",600,700,1},{"b",4600,4700,12}},true};crossing.nets={cross};Router cr(crossing,c,trace);cr.run();cr.validate(cross,cr.routes[0]);
    Net fixed{"fixed",{{"a",600,700,10},{"b",600,700,11}},true};crossing.nets={fixed};Router lock(crossing,c,trace);lock.run();require(lock.routes[0].tsv==lock.id(0,0,10),"New architecture moved fixed TSV");
    // A deliberately bad but legal committed route. Negotiated prices must
    // discover a new path, not merely delete cells from the old route.
    Design jam=tiny();Net net{"jam",{{"a",600,700,1},{"b",4600,700,1}},false};jam.nets={net};Config nc=c;nc.v3_stage=2;Router negotiated(jam,nc,trace);
    std::vector<Id> detour{negotiated.id(0,0,1),negotiated.id(0,0,2),negotiated.id(0,1,2),negotiated.id(0,2,2),negotiated.id(0,2,3),negotiated.id(1,2,3),negotiated.id(2,2,3),negotiated.id(3,2,3),negotiated.id(4,2,3),negotiated.id(4,2,2),negotiated.id(4,1,2),negotiated.id(4,0,2),negotiated.id(4,0,1)};
    Route old_route;for(Id v:detour)old_route.nodes.insert(v);for(size_t k=1;k<detour.size();k++)old_route.edges.insert({std::min(detour[k-1],detour[k]),std::max(detour[k-1],detour[k])});negotiated.make_guides(old_route);negotiated.validate(net,old_route);
    auto reset_route=[&]{if(!negotiated.routes[0].nodes.empty())negotiated.occupy(negotiated.routes[0],-1);negotiated.routes[0]=old_route;negotiated.occupy(old_route,1);};reset_route();
    Id hot=negotiated.id(2,2,3);negotiated.resource(hot).fixed=negotiated.resource(hot).capacity;
    double old_cost=negotiated.guide_grid_cost(old_route);RouterTestAccess::negotiated(negotiated,1);negotiated.validate(net,negotiated.routes[0]);
    require(negotiated.guide_grid_cost(negotiated.routes[0])<old_cost&&negotiated.overflow()==0,"Negotiation did not improve the legal detour");
    require(negotiated.routes[0].nodes.count(negotiated.id(2,0,1)),"Negotiation was only subset pruning");
    reset_route();auto original_resources=negotiated.resources;auto original_json=negotiated.route_json(net,old_route).dump();
    struct Fault:std::stringbuf{Router&router;bool soft,triggered=false;Fault(Router&r,bool value):router(r),soft(value){}std::streamsize xsputn(const char*s,std::streamsize n)override{
        std::string text(s,size_t(n));if(!triggered&&text.find(soft?"search_started":"net_added")!=std::string::npos){triggered=true;if(soft)RouterTestAccess::expire_pass(router);else throw std::runtime_error("injected negotiated commit failure");}return std::stringbuf::xsputn(s,n);}};
    for(bool soft:{false,true}){
        Fault buffer(negotiated,soft);trace.out=new std::ofstream;static_cast<std::ostream&>(*trace.out).rdbuf(&buffer);trace.out->exceptions(std::ios::badbit|std::ios::failbit);
        bool threw=false;try{RouterTestAccess::negotiated(negotiated,1);}catch(const std::runtime_error&){threw=true;}
        trace.out->exceptions(std::ios::goodbit);delete trace.out;trace.out=nullptr;
        require(buffer.triggered&&threw!=soft,"Negotiated failure/budget fixture did not run");
        require(negotiated.route_json(net,negotiated.routes[0]).dump()==original_json,"Negotiated rollback changed Route/Guide");
        for(const auto&[v,s]:negotiated.resources){auto found=original_resources.find(v);require(s.usage==(found==original_resources.end()?0:found->second.usage),"Negotiated rollback changed Usage");}
    }
    auto zero=negotiated.id(3,3,1);auto&s=negotiated.resource(zero);s.capacity=0;s.usage=4;s.fixed=3;s.history=2;
    require(RouterTestAccess::v3price(negotiated,zero,2)>=RouterTestAccess::v3price(negotiated,zero,1)&&RouterTestAccess::v3price(negotiated,zero,1)>0,"Normalized price is negative or ignores two-unit TSV demand");
}
