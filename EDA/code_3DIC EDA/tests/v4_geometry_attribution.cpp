// Read-only geometry Oracle for M1: separate Pin/OBS bitmaps, then union.
#include "physical_resources.hpp"
#include <iostream>
using namespace gr;
int main(int argc,char**argv){try{
    if(argc!=4)throw std::runtime_error("LEF DEF output.json required");Design d;parse_lef(argv[1],d);parse_def(argv[2],d);PhysicalModel model(d);
    const auto&g=model.grid;const auto&t=model.tracks[1];bool horizontal=d.layers.at(1).direction=="HORIZONTAL";size_t words=1;for(int c:model.capacities[1])words=std::max(words,(size_t(c)+63)/64);
    if(size_t(g.plane)*words*16>128*1024*1024)throw std::runtime_error("M1 independent bitmap workspace >128MiB");
    std::vector<uint64_t>pins(size_t(g.plane)*words),obs(pins.size());size_t shapes=0,no_track=0;
    physical_rectangles(d,[&](Rect r,bool pin){if(r.layer!=1)return;++shapes;g.cells(r,[&](Id v,int x,int y,Rect q){
        const auto&axis=horizontal?g.ys:g.xs;int index=horizontal?y:x;double lo=horizontal?q.y1:q.x1,hi=horizontal?q.y2:q.x2;
        auto cb=std::lower_bound(t.begin(),t.end(),axis[index]),ce=index==int(axis.size())-2?std::upper_bound(t.begin(),t.end(),axis[index+1]):std::lower_bound(t.begin(),t.end(),axis[index+1]);
        auto a=std::max(cb,std::lower_bound(t.begin(),t.end(),lo)),b=std::min(ce,std::upper_bound(t.begin(),t.end(),hi));auto&bits=pin?pins:obs;size_t base=size_t(v%g.plane)*words;
        auto mark=[&](size_t k){bits[base+k/64]|=uint64_t(1)<<(k%64);};
        if(a<b)for(auto k=a;k!=b;++k)mark(size_t(k-cb));else if(pin){++no_track;if(cb==ce)mark(0);else{double m=(lo+hi)/2;auto k=std::lower_bound(cb,ce,m);if(k==ce)--k;else if(k!=cb&&m-*(k-1)<=*k-m)--k;mark(size_t(k-cb));}}
    });});
    long long pin=0,ob=0,shared=0,total=0,baseline=0;size_t cells=0;
    auto count=[](uint64_t v){int n=0;while(v){v&=v-1;++n;}return n;};
    for(int y=0;y<g.ny;y++)for(int x=0;x<g.nx;x++){int f=0;for(size_t w=0;w<words;w++){size_t i=(size_t(y)*g.nx+x)*words+w;pin+=count(pins[i]);ob+=count(obs[i]);shared+=count(pins[i]&obs[i]);f+=count(pins[i]|obs[i]);}total+=f;cells+=f>0;baseline+=std::max(0,f-model.capacity(x,y,1));}
    Json j=Json::object();j["pin_track_demand"]=double(pin);j["obs_track_demand"]=double(ob);j["shared_pin_obs_tracks"]=double(shared);j["fixed_union_track_demand"]=double(total);j["fixed_baseline_overflow"]=double(baseline);j["fixed_cells"]=double(cells);j["shapes"]=double(shapes);j["no_track_pin_cell_shapes"]=double(no_track);j["workspace_bytes"]=double(pins.size()*16);j["official_attribution"]=false;write_json(argv[3],j);std::cout<<j.dump()<<'\n';return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
