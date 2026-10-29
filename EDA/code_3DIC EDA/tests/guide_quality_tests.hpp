void guide_quality_tests(){
    Trace trace("",1);Design d=tiny();Config c;c.iterations=0;Router r(d,c,trace);
    Route path;path.guides={{100,200,2100,1200,1},{1100,200,2100,3200,2}};
    require(r.guide_wirelength(path)==1500,"Guide center-distance proxy");
    Route corner;corner.guides={{100,200,1100,1200,1},{1100,1200,2100,2200,2}};
    require(!std::isfinite(r.guide_wirelength(corner)),"Guide proxy used corner via");
    Route side;side.guides={{100,200,2100,1200,1},{100,1200,2100,2200,1}};
    require(!std::isfinite(r.guide_wirelength(side)),"Guide proxy used nonpreferred side connection");
    Route large;large.guides.resize(1025);require(!std::isfinite(r.guide_wirelength(large)),"Guide estimate work limit ignored");
    Design geometry=tiny();Macro m;m.width=m.height=1;m.pins["A"]={{.1,.2,.9,.7,1}};m.obs=m.pins["A"];geometry.macros["B"]=m;geometry.instances["u"]={"u","B","N",100,200};
    Router shapes(geometry,c,trace);shapes.build_fixed_demand();auto before=shapes.resources;shapes.build_guide_model();
    Id node=shapes.id(0,0,1);int merged=shapes.guide_fixed_demand(node);
    require(merged>0&&shapes.resource(node).fixed==2*merged,"Physical fixed-shape union did not remove duplicate projections");
    for(const auto&[v,old]:before)require(shapes.resource(v).fixed==old.fixed,"Union model rewrote original fixed demand");
    Config final=c;final.v3_final=true;final.tree_route_mode=1;final.net_order_mode=2;final.congestion_cost_mode=2;
    Router selected(d,final,trace);require(selected.cfg.tree_route_mode==0&&selected.cfg.net_order_mode==0&&selected.cfg.congestion_cost_mode==0&&selected.cfg.reroute_policy_mode==0&&selected.cfg.v3_final,"Final strategy identity or baseline selection");
    d.nets={{"f",{{"p",600,700,1},{"q",4600,700,1}},false}};Router run(d,final,trace);run.run();
    require(result(run).at("v3_final").at("strategy").string()=="v3_final_guide_guard","Final strategy identity missing");
    run.validate(d.nets[0],run.routes[0]);
    Design loop=tiny();loop.nets={{"loop",{{"a",600,700,1},{"b",4600,700,1}},false}};Router pruning(loop,c,trace);
    Route original=pruning.route(loop.nets[0]);std::vector<Id> detour{pruning.id(0,0,1),pruning.id(0,0,2),pruning.id(0,1,2),pruning.id(0,2,2),pruning.id(0,2,3),pruning.id(1,2,3),pruning.id(2,2,3),pruning.id(3,2,3),pruning.id(4,2,3),pruning.id(4,2,2),pruning.id(4,1,2),pruning.id(4,0,2),pruning.id(4,0,1)};
    for(Id v:detour)original.nodes.insert(v);
    for(size_t i=1;i<detour.size();i++)original.edges.insert({std::min(detour[i-1],detour[i]),std::max(detour[i-1],detour[i])});
    original.guides.clear();pruning.make_guides(original);pruning.validate(loop.nets[0],original);pruning.routes[0]=original;pruning.occupy(original,1);
    auto resources=pruning.resources;RouterTestAccess::prune(pruning);require(pruning.routes[0].nodes.size()<original.nodes.size(),"Subset pruning did not remove redundant loop");pruning.validate(loop.nets[0],pruning.routes[0]);
    for(const auto&[v,res]:pruning.resources)require(res.usage>=0&&res.usage<=resources.at(v).usage,"Pruning increased demand");
    pruning.occupy(pruning.routes[0],-1);pruning.routes[0]=original;pruning.occupy(original,1);
    struct Failure:std::stringbuf{std::streamsize xsputn(const char*s,std::streamsize n)override{if(std::string(s,size_t(n)).find("net_added")!=std::string::npos)throw std::runtime_error("injected prune trace failure");return std::stringbuf::xsputn(s,n);}} buffer;
    trace.out=new std::ofstream;static_cast<std::ostream&>(*trace.out).rdbuf(&buffer);trace.out->exceptions(std::ios::badbit|std::ios::failbit);
    bool failed=false;try{RouterTestAccess::prune(pruning);}catch(const std::exception&){failed=true;}
    trace.out->exceptions(std::ios::goodbit);delete trace.out;trace.out=nullptr;
    require(failed,"Prune failure injection missed replacement");require(pruning.route_json(loop.nets[0],pruning.routes[0]).dump()==pruning.route_json(loop.nets[0],original).dump(),"Prune rollback changed Route/Guide");
    for(const auto&[v,res]:pruning.resources)require(res.usage==resources.at(v).usage,"Prune exception rollback changed demand");
    // An actual tree can still contain unused legal adjacency between its
    // occupied cells. This is the common case in MST branch unions.
    Route winding=original;winding.edges.erase({pruning.id(1,0,1),pruning.id(2,0,1)});
    require(winding.edges.size()+1==winding.nodes.size(),"Induced-shortcut fixture is not a tree");
    pruning.validate(loop.nets[0],winding);pruning.routes[0]=winding;
    RouterTestAccess::prune(pruning);pruning.validate(loop.nets[0],pruning.routes[0]);
    require(pruning.routes[0].nodes.size()<winding.nodes.size(),"Unused legal adjacency was ignored");
    for(const auto&[v,res]:pruning.resources)require(res.usage>=0&&res.usage<=resources.at(v).usage,"Induced shortcut increased demand");
    Design crossing=loop;crossing.nets[0].cross=true;crossing.nets[0].pins.push_back({"top",600,700,11});
    Router tsv(crossing,c,trace);Route cross=tsv.route(crossing.nets[0]);
    for(Id v:detour)cross.nodes.insert(v);
    for(size_t i=1;i<detour.size();i++)cross.edges.insert({std::min(detour[i-1],detour[i]),std::max(detour[i-1],detour[i])});
    cross.guides.clear();tsv.make_guides(cross);tsv.validate(crossing.nets[0],cross);tsv.routes[0]=cross;tsv.occupy(cross,1);
    RouterTestAccess::prune(tsv);tsv.validate(crossing.nets[0],tsv.routes[0]);
    require(tsv.routes[0].nodes.size()<cross.nodes.size()&&tsv.routes[0].tsv==cross.tsv,"TSV pruning did not preserve the interface anchor");
    int tx,ty,tl;tsv.decode(cross.tsv,tx,ty,tl);Id upper=tsv.id(tx,ty,11);
    for(const auto&[v,res]:tsv.resources){int expected=int(tsv.routes[0].nodes.count(v))+int(v==cross.tsv||v==upper);require(res.usage==expected,"TSV pruning lost or duplicated interface demand");}
}
