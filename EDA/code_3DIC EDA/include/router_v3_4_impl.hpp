#pragma once
// Announcement-derived PHYSICAL model. Never described as an official score.
namespace gr {
std::vector<Rect> Router::pin_geometry(const Pin&p)const{
    auto io=d.io_pin_rects.find(p.name);if(io!=d.io_pin_rects.end())return io->second;
    auto dot=p.name.rfind('.');
    if(dot!=std::string::npos){
        auto instance=d.instances.find(p.name.substr(0,dot));
        if(instance!=d.instances.end()){
            const auto&m=d.macros.at(instance->second.macro);auto pin=m.pins.find(p.name.substr(dot+1));
            if(pin!=m.pins.end()){std::vector<Rect> out;for(const auto&r:pin->second)out.push_back(placed_rect(r,m,instance->second,d.dbu));return out;}
        }
    }
    // Synthetic graph fixtures may specify only an access point. Real parsed
    // terminals always resolve above; do not invent an enlarged rectangle.
    return {{p.x,p.y,p.x,p.y,p.layer}};
}
void Router::geometry_cells(Rect r,const std::function<void(Id,const Rect&)>&visit)const{
    if(!d.layers.count(r.layer))return;
    if(!std::isfinite(r.x1)||!std::isfinite(r.y1)||!std::isfinite(r.x2)||!std::isfinite(r.y2)||r.x2<r.x1||r.y2<r.y1)throw std::runtime_error("Invalid physical Pin/OBS rectangle");
    if(r.x2<xs.front()||r.x1>xs.back()||r.y2<ys.front()||r.y1>ys.back())return;
    r.x1=std::max(r.x1,xs.front());r.x2=std::min(r.x2,xs.back());r.y1=std::max(r.y1,ys.front());r.y2=std::min(r.y2,ys.back());
    int xa=locate(xs,r.x1),xb=locate(xs,r.x2>r.x1?std::nextafter(r.x2,r.x1):r.x2);
    int ya=locate(ys,r.y1),yb=locate(ys,r.y2>r.y1?std::nextafter(r.y2,r.y1):r.y2);
    for(int y=ya;y<=yb;y++)for(int x=xa;x<=xb;x++){
        Id v=id(x,y,r.layer);auto c=cell(v);Rect q{std::max(c.x1,r.x1),std::max(c.y1,r.y1),std::min(c.x2,r.x2),std::min(c.y2,r.y2),r.layer};visit(v,q);
    }
}
std::vector<Id> Router::own_pin_cells(const Net&n)const{
    std::vector<Id> out;
    for(const auto&p:n.pins)for(auto r:pin_geometry(p))geometry_cells(r,[&](Id v,const Rect&){out.push_back(v);});
    std::sort(out.begin(),out.end());out.erase(std::unique(out.begin(),out.end()),out.end());return out;
}
void Router::build_v34_model(){
    if(v34_model_ready_)return;
    if(cfg.v3_geometry_bounded){build_v35_model();return;}
    batch_cache_ready_=false;v3_fixed_cache_.clear();guide_model_ready_=false;v34_tsv_blocked_.assign(size_t(nx)*ny,0);
    size_t coordinates=0;
    for(int l=1;l<=20;l++){
        auto&t=scoring_tracks_[l];t.clear();if(!d.layers.count(l))continue;
        for(const auto&track:d.tracks)if(track.layer==l&&track.axis==(layer_horizontal_[l]?'Y':'X')){
            if(coordinates+size_t(track.count)>4000000)throw std::runtime_error("V3.4 physical Track table exceeds 4M coordinate safety budget");
            coordinates+=size_t(track.count);for(int k=0;k<track.count;k++)t.push_back(track.start+k*track.step);
        }
        std::sort(t.begin(),t.end());t.erase(std::unique(t.begin(),t.end()),t.end());
    }
    // Sparse cell -> merged Track-index intervals. The virtual index -1 is
    // used only for a Pin in a cell with ZERO real Tracks; Capacity stays zero.
    std::map<Id,std::vector<std::pair<int,int>>> bins;
    size_t intervals=0;constexpr size_t limit=2000000;
    auto add=[&](Rect r,bool pin){geometry_cells(r,[&](Id v,const Rect&q){
        if(++intervals>limit)throw std::runtime_error("V3.4 physical geometry exceeds 2M cell-interval safety budget (no legacy fallback)");
        if(intervals%4096==0)check_time();
        v34_tsv_blocked_[size_t(v%(Id(nx)*ny))]=1;
        int x,y,l;decode(v,x,y,l);const auto&t=scoring_tracks_[l];bool horizontal=layer_horizontal_[l];auto c=cell(v);
        double lo=horizontal?q.y1:q.x1,hi=horizontal?q.y2:q.x2,clo=horizontal?c.y1:c.x1,chi=horizontal?c.y2:c.x2;
        auto cb=std::lower_bound(t.begin(),t.end(),clo),ce=chi==(horizontal?ys.back():xs.back())?std::upper_bound(t.begin(),t.end(),chi):std::lower_bound(t.begin(),t.end(),chi);
        auto a=std::max(cb,std::lower_bound(t.begin(),t.end(),lo)),b=std::min(ce,std::upper_bound(t.begin(),t.end(),hi));
        if(a<b)bins[v].emplace_back(int(a-t.begin()),int(b-t.begin()));
        else if(pin){
            ++v34_pin_min_track_;
            // The announcement does not specify WHICH Track. Assign the
            // nearest in-cell Track deterministically, then union with OBS
            // and all other Pins. This is a labelled modelling assumption.
            if(cb==ce)bins[v].emplace_back(-1,0);
            else{double middle=(lo+hi)/2;auto k=std::lower_bound(cb,ce,middle);if(k==ce)--k;else if(k!=cb&&middle-*(k-1)<=*k-middle)--k;int index=int(k-t.begin());bins[v].emplace_back(index,index+1);}
        }
    });};
    for(const auto&[name,i]:d.instances){
        (void)name;check_time();const auto&m=d.macros.at(i.macro);
        for(auto r:m.obs)add(placed_rect(r,m,i,d.dbu),false);
        for(const auto&[pn,rs]:m.pins){(void)pn;for(auto r:rs)add(placed_rect(r,m,i,d.dbu),true);}
    }
    for(const auto&[name,rs]:d.io_pin_rects){(void)name;for(auto r:rs)add(r,true);}
    for(const auto&n:d.nets)for(const auto&p:n.pins){
        auto dot=p.name.rfind('.');bool known=d.io_pin_rects.count(p.name)||(dot!=std::string::npos&&d.instances.count(p.name.substr(0,dot)));
        if(!known)for(auto r:pin_geometry(p))add(r,true);
    }
    v34_model_ready_=true;
    for(auto&[v,res]:resources){res.fixed=0;res.capacity=scoring_capacity(v);res.scoring_ready=false;}
    for(auto&[v,ranges]:bins){
        std::sort(ranges.begin(),ranges.end());int count=0,end=-2;
        for(auto[a,b]:ranges){count+=std::max(0,b-std::max(a,end));end=std::max(end,b);}
        auto&res=resource(v);res.fixed=count;res.scoring_ready=false;
    }
    v34_fixed_cells_=bins.size();guide_interval_count_=intervals;guide_model_ready_=true;
    trace.event("rules_20261029_model_built",rules_20261029_summary());
}
bool Router::tsv_legal(Id v)const{
    if(!cfg.v3_rules_20261029)return true;
    if(!v34_model_ready_)return false;
    int x,y,l;decode(v,x,y,l);
    if(l!=10||x<0||x>=nx||y<0||y>=ny||!d.tsv_cut||!d.layers.count(10)||!d.layers.count(11))return false;
    if(v34_tsv_blocked_[size_t(y)*nx+x])return false;
    auto[cx,cy]=center(v);double half=2.5*d.dbu;
    return cx-half>=xs.front()&&cx+half<=xs.back()&&cy-half>=ys.front()&&cy+half<=ys.back();
}
void Router::prepare_v34_route(const Net&n,Route&r)const{
    r.demand_exempt=own_pin_cells(n);r.tsv_demand.clear();r.resource_demand.clear();
    if(r.cross){
    if(!tsv_legal(r.tsv))throw std::runtime_error("Illegal TSV Pin/OBS GCell: "+n.name);
    auto[cx,cy]=center(r.tsv);double half=2.5*d.dbu;
    for(int l:{10,11})geometry_cells({cx-half,cy-half,cx+half,cy+half,l},[&](Id v,const Rect&q){
        const auto&t=scoring_tracks_[l];auto c=cell(v);bool h=layer_horizontal_[l];double lo=h?q.y1:q.x1,hi=h?q.y2:q.x2,clo=h?c.y1:c.x1,chi=h?c.y2:c.x2;
        auto cb=std::lower_bound(t.begin(),t.end(),clo),ce=chi==(h?ys.back():xs.back())?std::upper_bound(t.begin(),t.end(),chi):std::lower_bound(t.begin(),t.end(),chi);
        auto a=std::max(cb,std::lower_bound(t.begin(),t.end(),lo)),b=std::min(ce,std::upper_bound(t.begin(),t.end(),hi));
        // Positive area but no Track is explicit zero-capacity demand, not a
        // fixed two-unit TSV charge and not a fabricated capacity increase.
        r.tsv_demand.emplace_back(v,std::max(1,int(b-a)));
    });
    std::sort(r.tsv_demand.begin(),r.tsv_demand.end());
    }
    std::map<Id,int> demand;
    auto exempt=[&](Id v){return std::binary_search(r.demand_exempt.begin(),r.demand_exempt.end(),v);};
    for(Id v:r.nodes)if(!exempt(v))demand[v]=1;
    for(auto[v,n]:r.tsv_demand)if(!exempt(v))demand[v]=std::max(demand[v],n);
    r.resource_demand.assign(demand.begin(),demand.end());
}
const std::vector<std::pair<Id,int>>& Router::v34_usage(const Route&r)const{
    // Prepared before a transaction starts: rollback must allocate NOTHING,
    // even when bad_alloc is the failure that triggered rollback.
    return r.resource_demand;
}
void Router::validate_v34(const Net&n,const Route&r)const{
    auto fail=[&](const std::string&reason){throw std::runtime_error("20261029 Net "+n.name+": "+reason);};
    if(n.pins.empty())fail("no Pins");
    if(r.cross!=n.cross)fail("cross-Die flag mismatch");
    if(r.cross&&!tsv_legal(r.tsv))fail("TSV in Pin/OBS GCell or invalid 5um footprint");
    if(!r.cross&&(r.tsv||!r.tsv_demand.empty()))fail("inner Net has TSV metadata");
    std::set<Id> covered;
    for(const auto&g:r.guides){
        std::ostringstream detail;detail<<"Layer "<<g.layer<<" rect ["<<g.x1<<','<<g.y1<<','<<g.x2<<','<<g.y2<<"] ";
        if(!d.layers.count(g.layer)||!std::isfinite(g.x1)||!std::isfinite(g.y1)||!std::isfinite(g.x2)||!std::isfinite(g.y2)||g.x1>=g.x2||g.y1>=g.y2||std::llround(g.x1)>=std::llround(g.x2)||std::llround(g.y1)>=std::llround(g.y2))fail(detail.str()+"invalid/degenerate Guide");
        if(g.x1<xs.front()||g.x2>xs.back()||g.y1<ys.front()||g.y2>ys.back())fail(detail.str()+"outside GCell domain");
        bool overlap=false;
        geometry_cells(g,[&](Id v,const Rect&q){if(q.x2<=q.x1||q.y2<=q.y1)return;overlap=true;if(!r.nodes.count(v))fail(detail.str()+"Guide covers non-route GCell "+std::to_string(v));covered.insert(v);});
        if(!overlap)fail(detail.str()+"no positive-area GCell overlap");
    }
    if(covered!=r.nodes)fail("Guide coverage differs from route nodes");
    for(Id v:r.nodes){int x,y,l;decode(v,x,y,l);if(x<0||x>=nx||y<0||y>=ny||!d.layers.count(l))fail("invalid node "+std::to_string(v));}
    for(const auto&e:r.edges)if(!r.nodes.count(e.a)||!r.nodes.count(e.b))fail("edge endpoint absent from route");
    for(Id v:r.steiner_points)if(!r.nodes.count(v))fail("disconnected Steiner branch node");
    if(r.demand_exempt!=own_pin_cells(n))fail("Pin demand exemption metadata mismatch");
    Route expected;expected.cross=r.cross;expected.tsv=r.tsv;expected.nodes=r.nodes;prepare_v34_route(n,expected);
    if(expected.tsv_demand!=r.tsv_demand)fail("TSV physical demand metadata mismatch");
    if(expected.resource_demand!=r.resource_demand)fail("cached physical occupancy metadata mismatch");
}
Json Router::rules_20261029_summary()const{
    Json j=Json::object();j["model"]="announcement_20261029_physical_candidate";j["official_new_evaluator_verified"]=false;
    j["tsv_exclusion_scope"]="any Pin/OBS positive-area overlap on ANY metal layer; conservative pending official clarification";
    j["tsv_width_um"]=5;j["blocked_xy_cells"]=double(std::count(v34_tsv_blocked_.begin(),v34_tsv_blocked_.end(),1));j["xy_cells"]=nx*ny;
    j["fixed_cells"]=double(v34_fixed_cells_);j["pin_zero_track_cell_shapes"]=double(v34_pin_min_track_);
    j["pin_zero_track_assignment"]="nearest real in-cell Track, or shared virtual token at zero Capacity; union with Pin/OBS";
    j["boundary_policy"]="positive-area rectangle overlap; half-open GCell Track ownership, closed outer die";
    j["congestion_formula"]="internal nonnegative marginal overflow potential, NOT the unprovided new official score formula";return j;
}
}
