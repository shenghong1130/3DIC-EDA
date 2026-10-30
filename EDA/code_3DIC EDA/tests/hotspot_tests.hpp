// Two competing Nets: the first may detour while the second sheds a large
// legal loop. Tests route real graph edges and compare the whole resource map.
void hotspot_tests(bool calibrated=false){
    Trace trace("",16);Design design=tiny();Config cfg;cfg.v3_stage=3;cfg.v3_batch=true;cfg.iterations=0;cfg.margin=100;cfg.congestion=1000;
    cfg.v3_calibrated=calibrated;
    design.nets={{"a",{{"a0",600,700,1},{"a1",4600,700,1}},false},{"b",{{"b0",600,700,1},{"b1",4600,700,1}},false}};
    Router router(design,cfg,trace);
    for(const auto&[layer,description]:design.layers){(void)description;for(int y=0;y<router.ny;y++)for(int x=0;x<router.nx;x++){auto&r=router.resource(router.id(x,y,layer));r.capacity=100;if(layer!=1&&layer!=2)r.fixed=100000;}}
    for(int y=0;y<5;y++)for(int x=0;x<5;x++)if(y>=2)router.resource(router.id(x,y,1)).fixed=100000;
    for(int x=0;x<5;x++)router.resource(router.id(x,1,1)).capacity=1;
    Id hot=router.id(2,0,1);router.resource(hot).capacity=1;
    auto path=[&](const std::vector<Id>&nodes){Route r;for(Id v:nodes)r.nodes.insert(v);for(size_t k=1;k<nodes.size();k++)r.edges.insert({std::min(nodes[k-1],nodes[k]),std::max(nodes[k-1],nodes[k])});router.make_guides(r);return r;};
    std::vector<Id> line;for(int x=0;x<5;x++)line.push_back(router.id(x,0,1));
    auto direct=path(line);auto loop=line;loop.push_back(router.id(4,0,2));for(int y=1;y<5;y++)loop.push_back(router.id(4,y,2));loop.push_back(router.id(4,4,1));for(int x=3;x>=0;x--)loop.push_back(router.id(x,4,1));loop.push_back(router.id(0,4,2));for(int y=3;y>=0;y--)loop.push_back(router.id(0,y,2));loop.push_back(router.id(0,0,1));
    auto old_loop=path(loop);std::vector<Route> original{direct,old_loop};
    for(size_t i=0;i<2;i++){router.validate(design.nets[i],original[i]);router.routes[i]=original[i];router.occupy(original[i],1);}
    auto usage=router.resources;double old_wire=router.guide_grid_cost(direct)+router.guide_grid_cost(old_loop);
    auto reset=[&]{for(const auto&r:router.routes)router.occupy(r,-1);router.routes=original;for(const auto&r:router.routes)router.occupy(r,1);router.cfg=cfg;RouterTestAccess::phase(router,false);};
    auto invariant=[&](bool same){
        std::map<Id,int> demand;for(size_t i=0;i<router.routes.size();i++){const auto&r=router.routes[i];router.validate(design.nets[i],r);for(Id v:r.nodes)++demand[v];if(r.cross){int x,y,l;router.decode(r.tsv,x,y,l);++demand[r.tsv];++demand[router.id(x,y,11)];}if(same)require(router.route_json(design.nets[i],r).dump()==router.route_json(design.nets[i],original[i]).dump(),"Batch rollback changed complete Route/Guide/Steiner/TSV");}
        for(const auto&[v,r]:router.resources){require(r.usage>=0&&r.usage==demand[v],"Batch shared resource Usage differs from independent sum");if(same)require(r.usage==(usage.count(v)?usage.at(v).usage:0),"Batch rollback lost resource demand");}
    };
    RouterTestAccess::batch_toll(router,hot,1000);double total=old_wire;Json detail;auto before=RouterTestAccess::batch_quality(router);
    require(RouterTestAccess::group(router,{0,1},total,detail),"Joint rerouting fixture did not improve");
    require(detail.at("individually_longer_nets").number()>0&&total<old_wire,"Batch did not allow an individual loss with a joint gain");
    require(RouterTestAccess::batch_quality(router).at("phi").number()<before.at("phi").number(),"Batch accepted a non-improving whole-map potential");invariant(false);
    std::cout<<"Hotspot batch fixture: individually longer Net accepted with whole-group wire "<<old_wire<<" -> "<<total<<" and lower global potential\n";
    reset();total=old_wire;detail=Json();(void)RouterTestAccess::group(router,{1,0},total,detail);invariant(false);
    // Inject failures AFTER the first candidate has installed real Usage.
    struct Hook:std::stringbuf {std::function<void(const std::string&)> action;std::streamsize xsputn(const char*s,std::streamsize n)override{std::string text(s,size_t(n));if(action)action(text);return std::stringbuf::xsputn(s,n);}};
    for(int fault=0;fault<6;fault++){
        reset();total=old_wire;bool fired=false;Hook hook;trace.out=new std::ofstream;static_cast<std::ostream&>(*trace.out).rdbuf(&hook);trace.out->exceptions(std::ios::badbit|std::ios::failbit);
        hook.action=[&](const std::string&s){if(!fired&&s.find(fault==5?"hotspot_group_committed":"hotspot_net_trial")!=std::string::npos){fired=true;if(fault==0)throw std::runtime_error("injected group fault");if(fault==1)throw std::bad_alloc();if(fault==2)router.cfg.time_limit=-1;if(fault==3)RouterTestAccess::expire_pass(router);if(fault==4)router.cfg.search_limit=1;if(fault==5)throw std::runtime_error("injected committed Trace fault");}};
        bool caught=false,accepted=false;try{accepted=RouterTestAccess::group(router,{0,1},total,detail);}catch(const std::exception&){caught=true;}
        trace.out->exceptions(std::ios::goodbit);delete trace.out;trace.out=nullptr;
        require(fired&&!accepted,"Batch fault injection missed a real trial");require(caught==(fault==0||fault==1||fault==2||fault==5),"Batch classified hard/soft failure incorrectly");
        require(total==old_wire,"Batch rollback changed global Guide wire total");invariant(true);
    }
    reset();total=old_wire;bool invalid=false;try{RouterTestAccess::group(router,{0,0},total,detail);}catch(const std::runtime_error&){invalid=true;}require(invalid,"Duplicate batch Net accepted");invariant(true);
    RouterTestAccess::hotspot(router);invariant(false);require(result(router).at("v3_algorithm").at("counters").at("hotspot_groups_attempted").number()>0,"Hotspot inverse index never formed a real group");
    // Fixed cross-Die routes exercise the TWO units at each TSV endpoint.
    Design crossing=tiny();crossing.nets={{"x",{{"lo",600,700,10},{"hi",600,700,11}},true},{"y",{{"lo",600,700,10},{"hi",600,700,11}},true}};
    Router cross(crossing,cfg,trace);for(size_t i=0;i<2;i++){cross.routes[i]=cross.route(crossing.nets[i]);cross.occupy(cross.routes[i],1);}auto saved=cross.routes;auto old_resources=cross.resources;
    Hook hook;trace.out=new std::ofstream;static_cast<std::ostream&>(*trace.out).rdbuf(&hook);trace.out->exceptions(std::ios::badbit|std::ios::failbit);bool fired=false;
    hook.action=[&](const std::string&s){if(!fired&&s.find("hotspot_net_trial")!=std::string::npos){fired=true;throw std::bad_alloc();}};double wire=0;for(const auto&r:cross.routes)wire+=cross.guide_grid_cost(r);
    try{RouterTestAccess::group(cross,{1,0},wire,detail);}catch(const std::exception&){}
    trace.out->exceptions(std::ios::goodbit);delete trace.out;trace.out=nullptr;require(fired,"TSV batch fault did not execute");
    for(size_t i=0;i<2;i++)require(cross.route_json(crossing.nets[i],cross.routes[i]).dump()==cross.route_json(crossing.nets[i],saved[i]).dump(),"Fixed TSV batch rollback changed route");
    for(auto&[v,r]:cross.resources)require(r.usage==(old_resources.count(v)?old_resources.at(v).usage:0)&&r.usage>=0,"TSV extra Usage was not restored");
    Design branched=tiny();branched.die={0,0,11000,11000,0};branched.grid['X'].clear();branched.grid['Y'].clear();for(int k=0;k<=11;k++){branched.grid['X'].push_back(k*1000);branched.grid['Y'].push_back(k*1000);}branched.layers[1].direction="VERTICAL";branched.layers[2].direction="HORIZONTAL";
    branched.nets={{"tree",{{"a",500,500,1},{"b",10500,500,1},{"c",5500,10500,1}},false},{"fixed",{{"lo",500,500,10},{"hi",500,500,11}},true}};
    Config sc=cfg;sc.congestion=0;Router tree(branched,sc,trace);for(const auto&[l,desc]:branched.layers){(void)desc;for(int y=0;y<tree.ny;y++)for(int x=0;x<tree.nx;x++)tree.resource(tree.id(x,y,l)).capacity=100;}tree.resource(tree.id(2,0,2)).history=1.5;
    for(size_t i=0;i<2;i++){tree.routes[i]=tree.route(branched.nets[i]);tree.occupy(tree.routes[i],1);}require(!tree.routes[0].steiner_points.empty(),"Batch Steiner rollback fixture did not contain real topology nodes");
    auto original_tree=tree.routes;Hook tree_hook;trace.out=new std::ofstream;static_cast<std::ostream&>(*trace.out).rdbuf(&tree_hook);trace.out->exceptions(std::ios::badbit|std::ios::failbit);bool tree_fired=false;
    tree_hook.action=[&](const std::string&s){if(!tree_fired&&s.find("hotspot_net_trial")!=std::string::npos){tree_fired=true;throw std::runtime_error("injected Steiner group failure");}};double tree_wire=tree.guide_grid_cost(tree.routes[0])+tree.guide_grid_cost(tree.routes[1]);
    try{RouterTestAccess::group(tree,{0,1},tree_wire,detail);}catch(const std::exception&){}
    trace.out->exceptions(std::ios::goodbit);delete trace.out;trace.out=nullptr;require(tree_fired,"Steiner batch fault did not execute");
    for(size_t i=0;i<2;i++){tree.validate(branched.nets[i],tree.routes[i]);require(tree.route_json(branched.nets[i],tree.routes[i]).dump()==tree.route_json(branched.nets[i],original_tree[i]).dump(),"Batch failed to restore Steiner metadata, vias or fixed TSV");}
}
