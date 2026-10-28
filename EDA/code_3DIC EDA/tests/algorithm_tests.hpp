// Small independent correctness oracles; included after tiny()/require().
namespace gr {
struct RouterTestAccess {
    static void wrap_generation(Router&r){r.search_generation_=UINT32_MAX;}
    static size_t workspace(const Router&r){return r.search_cells_.size();}
    static void frozen_capacity(Router&r,bool enabled){r.refresh_layers();r.refresh_capacities();r.static_frozen_=enabled;}
    static std::pair<long long,int> pass(Router&r,int iteration,long long best){return r.reroute_pass(iteration,best);}
};
}
double path_cost(Router&r,const std::vector<Id>&p){
    double cost=0;
    for(size_t i=1;i<p.size();i++){
        int x,y,l,xx,yy,ll;r.decode(p[i-1],x,y,l);r.decode(p[i],xx,yy,ll);
        require(std::abs(x-xx)+std::abs(y-yy)+std::abs(l-ll)==1,"Nonadjacent search edge");
        if(l==ll)require(r.d.layers.at(l).direction=="HORIZONTAL"?y==yy:x==xx,"Nonpreferred search edge");
        else require(r.d.vias.count({std::min(l,ll),std::max(l,ll)}),"Absent search via");
        auto&a=r.resource(p[i]);double u=std::max(0.0,double(a.usage+a.fixed+1-a.capacity))/std::max(1,a.capacity);
        auto[cx,cy]=r.center(p[i-1]);auto[tx,ty]=r.center(p[i]);
        cost+=(l==ll?r.cfg.wire*(std::abs(cx-tx)+std::abs(cy-ty))/r.unit:r.cfg.via)+r.cfg.congestion*u*u+r.cfg.history*a.history;
    }return cost;
}
// Dijkstra over the complete small test graph, independent of A* workspace.
double shortest_cost(Router&r,Id start,Id goal,int lo,int hi){
    using Entry=std::pair<double,Id>;std::priority_queue<Entry,std::vector<Entry>,std::greater<Entry>> q;
    std::map<Id,double> costs;costs[start]=0;q.push({0,start});
    while(!q.empty()){
        auto[c,v]=q.top();q.pop();if(c!=costs.at(v))continue;if(v==goal)return c;
        int x,y,l;r.decode(v,x,y,l);
        auto visit=[&](int xx,int yy,int ll){
            if(xx<0||xx>=r.nx||yy<0||yy>=r.ny||ll<lo||ll>hi||!r.d.layers.count(ll))return;
            if(ll!=l&&!r.d.vias.count({std::min(l,ll),std::max(l,ll)}))return;
            Id next=r.id(xx,yy,ll);double nc=c+path_cost(r,{v,next});auto it=costs.find(next);
            if(it==costs.end()||nc+1e-10<it->second){costs[next]=nc;q.push({nc,next});}
        };
        if(r.d.layers.at(l).direction=="HORIZONTAL"){visit(x-1,y,l);visit(x+1,y,l);}else{visit(x,y-1,l);visit(x,y+1,l);}
        visit(x,y,l-1);visit(x,y,l+1);
    }return 1e100;
}
void algorithm_tests(){
    Trace trace("",1);Config cfg;cfg.margin=1;cfg.iterations=0;
    Design d=tiny();d.grid['X']={100,400,1600,3500,5100};d.grid['Y']={200,900,2700,5200};
    Router r(d,cfg,trace);
    for(int y=0;y<r.ny;y++)for(int x=0;x<r.nx;x++){
        auto rect=r.cell(r.id(x,y,1));auto[cx,cy]=r.center(r.id(x,y,1));
        require(cx==(rect.x1+rect.x2)/2&&cy==(rect.y1+rect.y2)/2,"Nonuniform center cache");
    }
    r.resource(r.id(1,0,1)).usage=20;r.resource(r.id(2,1,2)).fixed=12;r.resource(r.id(0,1,2)).history=3;
    auto start=r.id(0,0,1),goal=r.id(3,2,2);
    auto p=r.search(start,goal,1,2,false);double expected=shortest_cost(r,start,goal,1,2);
    require(std::abs(path_cost(r,p)-expected)<1e-10,"A* differs from Dijkstra on dynamic costs");
    require(r.search(start,goal,1,2,false)==p,"Generation reuse changed tie order");
    RouterTestAccess::wrap_generation(r);require(r.search(start,goal,1,2,false)==p,"Generation wrap reused stale state");
    require(RouterTestAccess::workspace(r)<=8192,"Dense workspace exceeds bound");
    // Design edits through the public direct API must refresh layer/via tables.
    d.vias.clear();bool failed=false;try{r.search(start,goal,1,2,false);}catch(...){failed=true;}require(failed,"Stale legal-via cache");
    d.vias.insert({1,2});r.cfg.search_limit=1;failed=false;try{r.search(start,goal,1,2,false);}catch(const std::exception&e){failed=std::string(e.what()).find("expansion limit")!=std::string::npos;}require(failed,"Search budget ignored");
    r.cfg.search_limit=1500000;r.cfg.time_limit=-1;failed=false;try{r.search(start,goal,1,2,false);}catch(const RouterTimeout&){failed=true;}require(failed,"Search timeout ignored");
    Design wide=tiny();wide.die={0,0,10000,10000,0};wide.grid['X'].clear();wide.grid['Y'].clear();
    for(int k=0;k<=100;k++){wide.grid['X'].push_back(k*100);wide.grid['Y'].push_back(k*100);}
    wide.tracks.clear();for(int l=1;l<=20;l++){wide.tracks.push_back({'X',0,1,10001,l});wide.tracks.push_back({'Y',0,1,10001,l});}
    Router sparse(wide,cfg,trace);auto longpath=sparse.search(sparse.id(0,0,1),sparse.id(99,99,2),1,2,false);
    require(std::abs(path_cost(sparse,longpath)-198.5)<1e-10,"Sparse fallback path cost");
    require(RouterTestAccess::workspace(sparse)==0,"Large window allocated dense graph");
    std::vector<int> caps;for(int l=1;l<=20;l++)for(int y=0;y<r.ny;y++)for(int x=0;x<r.nx;x++)caps.push_back(r.capacity(r.id(x,y,l)));
    r.cfg.time_limit=600;RouterTestAccess::frozen_capacity(r,true);size_t ci=0;
    for(int l=1;l<=20;l++)for(int y=0;y<r.ny;y++)for(int x=0;x<r.nx;x++)require(r.capacity(r.id(x,y,l))==caps[ci++],"Axis capacity changed half-open tracks");
    RouterTestAccess::frozen_capacity(r,false);d.tracks.clear();require(r.capacity(start)==0,"Stale capacity outside run");
    Design td=tiny();Config tc=cfg;tc.thermal=1000;tc.wire=0;tc.congestion=0;
    Router therm(td,tc,trace);Net cross{"heat_mutation",{{"a",600,700,1},{"b",4600,4700,12}},true};
    std::fill(therm.heat.begin(),therm.heat.end(),25);therm.heat[0]=80;
    require(therm.select_tsv(cross)==therm.id(0,0,10),"Initial thermal selection");
    therm.heat[0]=25;therm.heat[24]=90;
    require(therm.select_tsv(cross)==therm.id(4,4,10),"Heat edits left stale extrema");
    therm.run();std::fill(therm.heat.begin(),therm.heat.end(),25);therm.heat[1]=70;
    require(therm.select_tsv(cross)==therm.id(1,0,10),"Run cache leaked into direct API");
    Net fixed{"fixed",{{"a",600,700,10},{"b",600,700,11}},true};require(therm.select_tsv(fixed)==therm.id(0,0,10),"Fixed TSV changed");
    fixed.pins.push_back({"c",4600,4700,10});fixed.pins.push_back({"d",4600,4700,11});
    failed=false;try{therm.select_tsv(fixed);}catch(...){failed=true;}require(failed,"Ambiguous fixed TSV accepted");
    Design undo=tiny();undo.nets={{"single",{{"a",600,700,1}},false},
        {"cross",{{"a",600,700,1},{"b",4600,4700,12}},true},
        {"untouched",{{"a",4600,4700,1},{"b",3600,4700,1}},false}};
    Router ur(undo,cfg,trace);ur.run();auto hot=ur.id(0,0,1);ur.resource(hot).fixed=ur.resource(hot).capacity+1;
    std::vector<std::string> before;for(size_t i=0;i<ur.routes.size();i++)before.push_back(ur.route_json(undo.nets[i],ur.routes[i]).dump());
    auto original_resources=ur.resources;auto original_overflow=ur.overflow();
    auto unchanged=[&]{
        for(size_t i=0;i<ur.routes.size();i++){require(ur.route_json(undo.nets[i],ur.routes[i]).dump()==before[i],"Undo changed route/guide/TSV");ur.validate(undo.nets[i],ur.routes[i]);}
        for(auto&[v,a]:ur.resources){auto it=original_resources.find(v);require(a.usage>=0,"Negative usage after undo");if(it==original_resources.end())require(a.usage==0&&a.fixed==0,"New resource occupied after undo");else require(a.usage==it->second.usage&&a.fixed==it->second.fixed&&a.capacity==it->second.capacity,"Undo occupancy/capacity/fixed mismatch");}
        require(ur.overflow()==original_overflow,"Undo overflow changed");
    };
    auto[now,changed]=RouterTestAccess::pass(ur,1,original_overflow);
    require(now>=original_overflow&&changed==2,"Rejected pass fixture did not exercise two routes");unchanged();
    require(ur.resource(hot).history>original_resources.at(hot).history,"History incorrectly rolled back");
    ur.cfg.search_limit=1;failed=false;try{RouterTestAccess::pass(ur,2,original_overflow);}catch(...){failed=true;}
    require(failed,"Injected reroute failure did not occur");unchanged();
    ur.cfg.search_limit=1500000;ur.cfg.time_limit=-1;failed=false;try{RouterTestAccess::pass(ur,3,original_overflow);}catch(const RouterTimeout&){failed=true;}
    require(failed,"Reroute timeout ignored");unchanged();
    Design improve=tiny();improve.nets={{"detour",{{"a",600,700,1},{"b",4600,700,1}},false}};
    Config ic=cfg;ic.congestion=100;Router ir(improve,ic,trace);ir.run();auto middle=ir.id(2,0,1);ir.resource(middle).fixed=ir.resource(middle).capacity;
    long long ob=ir.overflow();auto[on,oc]=RouterTestAccess::pass(ir,1,ob);require(oc==1&&on<ob&&ir.overflow()==on,"Improving pass not retained");ir.validate(improve.nets[0],ir.routes[0]);
    auto tf=std::filesystem::temp_directory_path()/("gr_undo_trace_"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count())+".jsonl");
    std::map<std::string,std::string> final_edges;
    {Trace real(tf.u8string(),32);Config rc=cfg;rc.iterations=2;Router replay(undo,rc,real);auto&h=replay.resource(replay.id(0,0,1));h.fixed=h.capacity+1;replay.run();
     for(size_t i=0;i<replay.routes.size();i++)final_edges[undo.nets[i].name]=replay.route_json(undo.nets[i],replay.routes[i]).at("edges").dump();}
    std::map<std::string,std::string> replay_edges;std::istringstream lines(read_file(tf));std::string line;int seq=-1;
    while(std::getline(lines,line)){auto e=Json::parse(line);require(e.at("seq").number()>seq,"Nonmonotone Trace sequence");seq=int(e.at("seq").number());auto name=e.at("event").string();if(name=="net_added")replay_edges[e.at("net").string()]=e.at("edges").dump();else if(name=="net_removed")replay_edges.erase(e.at("net").string());}
    require(replay_edges==final_edges,"Incremental rollback Trace replay mismatch");std::filesystem::remove(tf);
}
