// Uses the independent Dijkstra/path legality oracle in algorithm_tests.hpp.
void tree_aware_tests(){
    Trace trace("",1);Config cfg;cfg.iterations=0;cfg.tree_route_mode=1;cfg.margin=100;
    Design d=tiny();d.grid['X']={100,400,1600,3500,5100};d.grid['Y']={200,900,2700,5200};
    Router r(d,cfg,trace);r.resource(r.id(1,0,1)).usage=20;r.resource(r.id(1,1,2)).history=5;
    for(int k=0;k<12;k++){
        Id start=r.id(k%r.nx,(k/r.nx)%r.ny,1);
        std::unordered_set<Id> goals{r.id(0,2,1),r.id(3,0,2),r.id(2,1,1)};
        auto path=r.search_to_tree(start,goals,1,2,false);
        double expected=1e100;for(Id goal:goals)expected=std::min(expected,shortest_cost(r,start,goal,1,2));
        require(path.front()==start&&goals.count(path.back()),"Tree search endpoints");
        require(std::abs(path_cost(r,path)-expected)<1e-9,"Tree A* differs from full-grid Dijkstra");
        require(r.search_to_tree(start,goals,1,2,false)==path,"Tree search generation/tie stability");
        RouterTestAccess::wrap_generation(r);require(r.search_to_tree(start,goals,1,2,false)==path,"Tree generation wrap");
    }
    auto target=r.id(0,2,1);require(r.search_to_tree(target,{target},1,2,false)==std::vector<Id>{target},"Pin on tree searched again");
    auto must_fail=[&](auto action,const char*message){bool failed=false;try{action();}catch(const std::exception&){failed=true;}require(failed,message);};
    must_fail([&]{r.search_to_tree(target,{},1,2,false);},"Empty target accepted");
    must_fail([&]{r.search_to_tree(target,{r.id(0,0,11)},1,20,true);},"Cross-Die tree range accepted");
    d.vias.clear();must_fail([&]{r.search_to_tree(r.id(0,0,1),{r.id(2,1,2)},1,2,false);},"Tree used absent LEF via");d.vias.insert({1,2});
    r.cfg.search_limit=1;must_fail([&]{r.search_to_tree(r.id(0,0,1),{r.id(3,2,2)},1,2,false);},"Tree ignored expansion limit");
    r.cfg.search_limit=1500000;r.cfg.time_limit=-1;bool timeout=false;
    try{r.search_to_tree(target,{target},1,2,false);}catch(const RouterTimeout&){timeout=true;}require(timeout,"Tree ignored timeout at existing target");
    Design wide=tiny();wide.die={0,0,10000,10000,0};wide.grid['X'].clear();wide.grid['Y'].clear();wide.tracks.clear();
    for(int k=0;k<=100;k++){wide.grid['X'].push_back(k*100);wide.grid['Y'].push_back(k*100);}
    for(int l=1;l<=20;l++){wide.tracks.push_back({'X',0,1,10001,l});wide.tracks.push_back({'Y',0,1,10001,l});}
    Router sparse(wide,cfg,trace);auto sp=sparse.search_to_tree(sparse.id(0,0,1),{sparse.id(99,99,2),sparse.id(99,98,2)},1,2,false);
    require(std::abs(path_cost(sparse,sp)-197.5)<1e-9,"Sparse tree cost");require(RouterTestAccess::workspace(sparse)==0,"Tree allocated global dense workspace");
    require(RouterTestAccess::workspace(r)<=8192,"Tree dense workspace exceeds bound");
    Design route_design=tiny();Router tr(route_design,cfg,trace);
    std::vector<Net> nets{
        {"two",{{"a",600,700,1},{"b",4600,4700,2}},false},
        {"three",{{"a",600,700,1},{"b",4600,2700,1},{"c",1600,4700,1}},false},
        {"same_cell",{{"a",600,700,1},{"duplicate",650,750,1},{"c",4600,4700,2}},false},
        {"cross",{{"a",600,700,1},{"b",4600,4700,12},{"c",2600,2700,13}},true},
        {"fixed",{{"a",600,700,10},{"b",600,700,11},{"c",4600,4700,1}},true}};
    Net many{"many",{},false};for(int i=0;i<12;i++)many.pins.push_back({"p"+std::to_string(i),600.0+(i%5)*1000,700.0+((i*3)%5)*1000,1+i%2});nets.push_back(many);
    for(auto&net:nets){auto route=tr.route(net);tr.validate(net,route);tr.occupy(route,1);
        for(Id v:route.nodes){int x,y,l;tr.decode(v,x,y,l);int demand=1+(route.cross&&(v==route.tsv||v==tr.id(int(route.tsv%tr.nx),int(route.tsv/tr.nx%tr.ny),11)));require(tr.resource(v).usage==demand,"Shared tree node double charged");}
        tr.occupy(route,-1);for(auto&entry:tr.resources)require(entry.second.usage==0,"Tree rip-up left demand");
    }
    Design ordering=tiny();ordering.nets={
        {"short",{{"a",600,700,1},{"b",1600,700,1}},false},
        {"long",{{"a",600,700,1},{"b",4600,4700,2}},false},
        {"cross",{{"a",600,700,1},{"b",1600,700,12}},true},
        {"tie",{{"a",600,700,1},{"b",1600,700,1}},false}};
    Router ordered(ordering,cfg,trace);ordered.cfg.net_order_mode=1;
    require(RouterTestAccess::order(ordered)==std::vector<size_t>({1,0,2,3}),"HPWL ordering or stable tie");
    ordered.cfg.net_order_mode=2;require(RouterTestAccess::order(ordered).front()==2,"Composite ordering cross priority");
    ordered.run();for(size_t i=0;i<ordering.nets.size();i++){require(ordered.route_json(ordering.nets[i],ordered.routes[i]).at("net").string()==ordering.nets[i].name,"Net identity reordered");ordered.validate(ordering.nets[i],ordered.routes[i]);}
    Design undo=tiny();undo.nets={{"single",{{"a",600,700,1}},false},{"cross",{{"a",600,700,1},{"b",4600,4700,12}},true},{"safe",{{"a",4600,4700,1}},false}};
    Router ur(undo,cfg,trace);ur.run();ur.cfg.reroute_priority_mode=1;
    for(Id v:{ur.id(0,0,1),ur.routes[1].tsv})ur.resource(v).fixed=ur.resource(v).capacity+1;
    require(RouterTestAccess::priority(ur).front()==1,"Nonascending rollback fixture missing");
    std::vector<std::string> before;for(size_t i=0;i<ur.routes.size();i++)before.push_back(ur.route_json(undo.nets[i],ur.routes[i]).dump());auto resources=ur.resources;auto overflow=ur.overflow();
    auto unchanged=[&]{
        for(size_t i=0;i<ur.routes.size();i++){require(before[i]==ur.route_json(undo.nets[i],ur.routes[i]).dump(),"Priority rollback changed route");ur.validate(undo.nets[i],ur.routes[i]);}
        for(auto&[v,a]:ur.resources){auto it=resources.find(v);require(a.usage==(it==resources.end()?0:it->second.usage),"Priority rollback demand mismatch");require(a.usage>=0,"Priority rollback negative usage");if(it!=resources.end())require(a.capacity==it->second.capacity&&a.fixed==it->second.fixed,"Priority rollback changed fixed/capacity");}
        require(ur.overflow()==overflow,"Priority rollback overflow mismatch");
    };
    auto rejected=RouterTestAccess::pass(ur,1,0);require(rejected.second==2,"Priority rejection fixture did not modify two Nets");unchanged();
    ur.cfg.search_limit=1;must_fail([&]{RouterTestAccess::pass(ur,2,0);},"Priority reroute failure not injected");unchanged();
    ur.cfg.search_limit=1500000;ur.cfg.time_limit=-1;bool rollback_timeout=false;
    try{RouterTestAccess::pass(ur,3,0);}catch(const RouterTimeout&){rollback_timeout=true;}
    require(rollback_timeout,"Priority reroute timeout ignored");unchanged();
    Design improve=tiny();improve.nets={{"detour",{{"a",600,700,1},{"b",4600,700,1}},false}};
    Config ic=cfg;ic.reroute_priority_mode=1;ic.congestion=100;Router ir(improve,ic,trace);ir.run();
    auto middle=ir.id(2,0,1);ir.resource(middle).fixed=ir.resource(middle).capacity;
    auto old_overflow=ir.overflow();auto accepted=RouterTestAccess::pass(ir,1,old_overflow);
    require(accepted.second==1&&accepted.first<old_overflow&&ir.overflow()==accepted.first,"Priority improving reroute not retained");ir.validate(improve.nets[0],ir.routes[0]);
}
