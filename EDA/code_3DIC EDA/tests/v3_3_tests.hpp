// Independent geometry and full-resource oracles, not copies of dirty updates.
void v3_3_tests(){
    Trace trace("",16);Config cfg;cfg.v3_calibrated=true;cfg.v3_batch=true;cfg.v3_stage=3;cfg.iterations=0;cfg.margin=100;
    Design d=tiny();Macro m;m.width=m.height=1;m.pins["A"]={{.1,.35,.9,.65,1}};m.obs=m.pins["A"];d.macros["m"]=m;d.instances["u"]={"u","m","N",100,200};
    // Duplicate TRACK declarations must not double the compatibility capacity.
    d.tracks.push_back(d.tracks[1]);Router model(d,cfg,trace);model.build_guide_model();
    Id v=model.id(0,0,1);int expected=0;
    std::vector<double> tracks;for(int k=0;k<26;k++)tracks.push_back(200+200*k);
    for(size_t k=0;k+1<tracks.size();k++)expected+=tracks[k]<850&&tracks[k+1]>550;
    require(expected==3&&model.scoring_fixed_demand(v)==expected,"Track-INTERVAL oracle or Pin/OBS union differs");
    Design single=d;single.tracks.pop_back();Router point_model(single,cfg,trace);point_model.build_guide_model();
    require(point_model.guide_fixed_demand(v)==2,"Fixture does not distinguish track points from track intervals");
    require(model.scoring_capacity(v)==5,"Duplicate physical Track capacity not merged");
    // Different geometry boundaries, exact tracks and every grid boundary.
    for(double lo:{200.,201.,400.,550.,800.,1000.})for(double hi:{600.,850.,1000.,1200.})if(hi>lo){
        Design x=tiny();Macro a;a.width=a.height=1;a.pins["A"]={{.1,(lo-200)/1000,.9,(hi-200)/1000,1}};x.macros["a"]=a;x.instances["u"]={"u","a","N",100,200};Router r(x,cfg,trace);r.build_guide_model();
        int count=0;for(size_t k=0;k+1<tracks.size();k++)count+=tracks[k]<hi&&tracks[k+1]>lo;
        require(r.scoring_fixed_demand(r.id(0,0,1))==count,"Geometry-dependent residual was replaced by a uniform +1");
    }
    Design multilayer=tiny();multilayer.macros["m"]=m;multilayer.macros["m"].obs.clear();multilayer.macros["m"].pins["A"].push_back({.1,.35,.9,.65,3});multilayer.instances=d.instances;
    Router projected(multilayer,cfg,trace);projected.build_guide_model();require(projected.scoring_fixed_demand(projected.id(0,0,3))==0,"Current-ELF first-layer compatibility not separated from physical geometry");
    require(multilayer.macros["m"].pins["A"].back().layer==3,"Compatibility pricing mutated original PIN layer");
    Design jam=tiny();jam.nets={{"escape",{{"a",600,700,1},{"b",4600,700,1}},false}};Router escape(jam,cfg,trace);auto direct=escape.route(jam.nets[0]);
    auto trial=RouterTestAccess::alternative(escape,jam.nets[0],direct,2);escape.validate(jam.nets[0],trial);
    bool lifted=false;for(Id node:trial.nodes){int x,y,l;escape.decode(node,x,y,l);lifted|=l>=3;}
    require(lifted&&trial.nodes!=direct.nodes,"Layer escape did not produce a genuinely different legal path");
    Design sum=tiny();Router incremental(sum,cfg,trace);
    for(int l=1;l<=20;l++)for(int y=0;y<5;y++)for(int x=0;x<5;x++){auto&r=incremental.resource(incremental.id(x,y,l));r.fixed=(x+3*y+l)%9;r.capacity=5;}
    RouterTestAccess::quality_cache(incremental);
    auto oracle=[&]{double phi=0,upper=0;long long over=0;for(const auto&[node,r]:incremental.resources){(void)node;double a=std::max(0,r.usage+r.fixed-r.capacity),b=std::max(0,r.usage+r.fixed+1-r.capacity);phi+=a*a/25;upper+=b*b/25;over+=static_cast<long long>(a);}auto q=RouterTestAccess::batch_quality(incremental);require(std::abs(q.at("phi").number()-phi)<1e-8&&std::abs(q.at("upper").number()-upper)<1e-8&&q.at("overflow").number()==over,"Dirty update differs from independent all-resource sum");};
    for(int k=0;k<200;k++){Route r;r.nodes={incremental.id(k%5,(k/5)%5,10),incremental.id(k%5,(k/5)%5,11)};r.cross=true;r.tsv=incremental.id(k%5,(k/5)%5,10);incremental.occupy(r,1);oracle();incremental.occupy(r,-1);oracle();}
    auto full=RouterTestAccess::quality_full(incremental),cached=RouterTestAccess::batch_quality(incremental);require(std::abs(full.at("phi").number()-cached.at("phi").number())<1e-8,"Full-map Oracle differs after TSV undo");
    // Reuse the real-route exception/budget/bad_alloc/Trace commit/TSV/Steiner
    // transaction fixtures in the calibrated architecture as well as V3.2.
    hotspot_tests(true);
    Design high=tiny();Net n;n.name="high";for(int k=0;k<33;k++)n.pins.push_back({std::to_string(k),600,700,1});high.nets={n};Router bounded(high,cfg,trace);bounded.routes[0]=bounded.route(n);bounded.occupy(bounded.routes[0],1);auto original=bounded.route_json(n,bounded.routes[0]).dump();double wire=bounded.guide_grid_cost(bounded.routes[0]);Json detail;
    require(!RouterTestAccess::group(bounded,{0},wire,detail)&&detail.at("reason").string()=="high_pin_group_budget"&&bounded.route_json(n,bounded.routes[0]).dump()==original,"High-pin transaction failed to preserve complete fallback");
    std::cout<<"V3.3 track-interval / capacity / layer escape / 400 dirty TSV updates / transaction oracles passed\n";
}
