// Congestion model oracles and guarded Move/rollback fault injection.
void congestion_tests(){
    Trace trace("",1);Config cfg;cfg.iterations=0;Design d=tiny();Router r(d,cfg,trace);
    Resource s;s.capacity=100;s.usage=100;
    require(std::abs(RouterTestAccess::penalty(r,s)-.0004)<1e-12,"Legacy penalty changed");
    r.cfg.congestion_cost_mode=1;require(RouterTestAccess::penalty(r,s)==4,"Incremental overflow penalty");
    require(RouterTestAccess::penalty(r,s,2)==8,"TSV two-unit incremental demand");
    s.usage=98;require(RouterTestAccess::penalty(r,s)==0,"Unsaturated resource charged overflow");
    r.cfg.congestion_cost_mode=2;s.usage=100;require(std::abs(RouterTestAccess::penalty(r,s)-1.2004)<1e-10,"Hybrid penalty formula");
    r.cfg.congestion_scope_mode=1;require(std::abs(RouterTestAccess::penalty(r,s)-.0004)<1e-12,"Scoped cost changed initial routing");
    RouterTestAccess::phase(r,true);require(std::abs(RouterTestAccess::penalty(r,s)-1.2004)<1e-10,"Scoped cost not enabled in reroute");
    RouterTestAccess::phase(r,false);r.cfg.congestion_scope_mode=0;
    s={};require(std::isfinite(RouterTestAccess::penalty(r,s))&&RouterTestAccess::penalty(r,s)>=0,"Zero capacity penalty");
    s.capacity=10;s.fixed=1000;r.cfg.congestion_cost_mode=1;require(RouterTestAccess::penalty(r,s)==4,"Fixed baseline charged as incremental overflow");
    s.history=2;require(RouterTestAccess::penalty(r,s)==6,"History cost lost");
    d.grid['X']={100,400,1600,3500,5100};d.grid['Y']={200,900,2700,5200};
    for(int mode=0;mode<=2;mode++){
        Config c=cfg;c.congestion_cost_mode=mode;c.margin=100;Router oracle(d,c,trace);
        oracle.resource(oracle.id(1,0,1)).usage=20;oracle.resource(oracle.id(2,1,2)).fixed=20;
        for(int k=0;k<8;k++){Id a=oracle.id(k%oracle.nx,(k/oracle.nx)%oracle.ny,1),b=oracle.id(3,2,2);
            auto path=oracle.search(a,b,1,2,false);require(std::abs(path_cost(oracle,path)-shortest_cost(oracle,a,b,1,2))<1e-9,"Congestion A* differs from Dijkstra");
            auto tree=oracle.search_to_tree(a,{b,oracle.id(0,2,1)},1,2,false);
            require(std::abs(path_cost(oracle,tree)-std::min(shortest_cost(oracle,a,b,1,2),shortest_cost(oracle,a,oracle.id(0,2,1),1,2)))<1e-9,"Congestion tree heuristic inadmissible");
        }
    }
    // Fixed shapes are additive by existing policy. This proves overlap can
    // count twice; it deliberately does not change or claim official semantics.
    Design shapes=tiny();Macro macro;macro.width=macro.height=1;macro.pins["A"]={{.1,.2,.9,.7,1}};shapes.macros["B"]=macro;shapes.instances["u"]={"u","B","N",100,200};
    Router one(shapes,cfg,trace);one.build_fixed_demand();long long f1=0;for(auto&[v,res]:one.resources)f1+=res.fixed;
    shapes.macros["B"].obs=macro.pins["A"];Router two(shapes,cfg,trace);two.build_fixed_demand();long long f2=0;for(auto&[v,res]:two.resources)f2+=res.fixed;
    require(f1>0&&f2==2*f1,"Overlapping Pin/OBS diagnostic fixture");
    RouterTestAccess::frozen_capacity(two,true);for(auto&[v,res]:two.resources)require(two.capacity(v)==res.capacity,"Capacity cache mismatch with shapes");
    Design layout=tiny();layout.nets={{"low",{{"a",600,700,1},{"b",4600,700,1}},false},{"high",{{"c",600,4700,1},{"d",4600,4700,1}},false}};
    auto exercise=[&](int fault){
        Trace events("",16);Config c=cfg;c.reroute_policy_mode=1;c.congestion=100;c.reroute_wire_growth=0;c.congestion_cost_mode=2;c.congestion_scope_mode=1;
        Router guarded(layout,c,events);guarded.run();
        guarded.resource(guarded.id(2,0,1)).fixed=guarded.resource(guarded.id(2,0,1)).capacity;
        guarded.resource(guarded.id(2,4,1)).fixed=guarded.resource(guarded.id(2,4,1)).capacity+1;
        auto original=guarded.resources;std::vector<std::string> before;for(size_t i=0;i<guarded.routes.size();i++)before.push_back(guarded.route_json(layout.nets[i],guarded.routes[i]).dump());
        auto unchanged=[&]{for(size_t i=0;i<guarded.routes.size();i++){require(before[i]==guarded.route_json(layout.nets[i],guarded.routes[i]).dump(),"Guard rollback changed Route/Guide");guarded.validate(layout.nets[i],guarded.routes[i]);}for(auto&[v,res]:guarded.resources){auto it=original.find(v);require(res.usage==(it==original.end()?0:it->second.usage)&&res.usage>=0,"Guard rollback usage");}};
        if(fault==0){guarded.cfg.search_limit=1;RouterTestAccess::pass(guarded,1,guarded.overflow());unchanged();return;}
        // Buffer hooks cause a real timeout/exception after a committed
        // replacement, not merely at function entry. No files or sleeps.
        struct Hook:std::stringbuf {std::function<void(const std::string&)> action;std::streamsize xsputn(const char*s,std::streamsize n)override{std::string text(s,size_t(n));if(action)action(text);return std::stringbuf::xsputn(s,n);}} hook;
        events.out=new std::ofstream;static_cast<std::ostream&>(*events.out).rdbuf(&hook);events.out->exceptions(std::ios::badbit|std::ios::failbit);
        bool fired=false;hook.action=[&](const std::string&text){if(!fired&&text.find(fault==4?"net_removed":"net_added")!=std::string::npos){fired=true;if(fault==1)guarded.cfg.time_limit=-1;else if(fault==2)throw std::runtime_error("injected trace failure");else RouterTestAccess::expire_pass(guarded);}};
        bool caught=false;try{RouterTestAccess::pass(guarded,1,guarded.overflow());}catch(const std::exception&){caught=true;}
        events.out->exceptions(std::ios::goodbit);delete events.out;events.out=nullptr;
        Resource check;check.capacity=100;check.usage=100;require(std::abs(RouterTestAccess::penalty(guarded,check)-.01)<1e-12,"Reroute phase leaked after return/exception");
        require(fired,"Fault injection did not reach a replacement");
        if(fault==3||fault==4){require(!caught,"Soft pass deadline escaped Router");if(fault==4)unchanged();for(size_t i=0;i<guarded.routes.size();i++)guarded.validate(layout.nets[i],guarded.routes[i]);}
        else{require(caught,"Injected exception did not propagate");unchanged();}
    };
    exercise(0);exercise(1);exercise(2);exercise(3);exercise(4);
    Design limited=layout;for(int l=3;l<=20;l++)limited.layers.erase(l);limited.vias={{1,2}};
    Config lc=cfg;lc.reroute_policy_mode=1;lc.reroute_wire_growth=0;lc.congestion=1000;
    Router limit(limited,lc,trace);limit.run();for(int y:{0,4})limit.resource(limit.id(2,y,1)).fixed=limit.resource(limit.id(2,y,1)).capacity;
    auto original_route=limit.route_json(limited.nets[0],limit.routes[0]).dump();
    RouterTestAccess::pass(limit,1,limit.overflow());require(original_route==limit.route_json(limited.nets[0],limit.routes[0]).dump(),"Wire guard accepted longer detour");
    auto cooldown=RouterTestAccess::pass(limit,2,limit.overflow());require(cooldown.second==0,"Unproductive Net rerouted during cooldown");
    limit.cfg.reroute_max_nets=1;auto capped=RouterTestAccess::pass(limit,3,limit.overflow());require(capped.second<=1,"Per-pass Net budget ignored");
    // Crossing route rollback keeps both extra TSV units and arbitrary order.
    Design cross=tiny();cross.nets={{"x",{{"a",600,700,1},{"b",4600,4700,12}},true}};
    Config cc=cfg;cc.reroute_policy_mode=1;cc.reroute_wire_growth=0;Router cr(cross,cc,trace);cr.run();
    for(Id v:cr.routes[0].nodes)cr.resource(v).fixed=cr.resource(v).capacity+1;
    auto saved=cr.route_json(cross.nets[0],cr.routes[0]).dump();auto res=cr.resources;cr.cfg.search_limit=1;
    RouterTestAccess::pass(cr,1,0);require(saved==cr.route_json(cross.nets[0],cr.routes[0]).dump(),"Guard TSV rollback route");
    for(auto&[v,value]:cr.resources)require(value.usage==(res.count(v)?res.at(v).usage:0),"Guard TSV extra demand not restored");
}
