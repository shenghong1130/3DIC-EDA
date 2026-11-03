#pragma once
// Independent 20261103 candidate model. The frozen 20261029 kernel is unchanged.
// The official replacement Track/GCell tie breaker is NOT published.
#include "physical_resources.hpp"
namespace gr {
enum class PinTrackPolicy {NearestInCell, LowerInCell, UpperInCell};
template<class Iterator> int no_track_pin_index(Iterator first,Iterator last,double center,PinTrackPolicy policy){
    if(first==last)throw std::runtime_error("No real Track: caller must use shared virtual token");
    auto upper=std::lower_bound(first,last,center);
    if(upper==last)return int(last-first)-1;
    if(upper==first||*upper==center)return int(upper-first);
    if(policy==PinTrackPolicy::LowerInCell)return int(upper-first)-1;
    if(policy==PinTrackPolicy::UpperInCell)return int(upper-first);
    return int(upper-first)-(center-*(upper-1)<=*upper-center?1:0);
}
struct PhysicalModel20261103 {
    PhysicalGrid grid;std::array<std::vector<double>,21> tracks;
    std::array<std::vector<int>,21> capacities;
    std::vector<unsigned char> blocked;
    PinTrackPolicy pin_policy=PinTrackPolicy::NearestInCell;
    size_t coordinates=0,intervals=0,rectangles=0,min_pin_shapes=0,fixed_cells=0,max_workspace_bytes=0,spool_bytes=0;
    explicit PhysicalModel20261103(const Design&d,PinTrackPolicy policy=PinTrackPolicy::NearestInCell):grid(d),blocked(size_t(grid.plane),0){
        pin_policy=policy;
        for(const auto&[l,layer]:d.layers){auto&t=tracks[l];for(const auto&r:d.tracks)if(r.layer==l&&r.axis==(layer.direction=="HORIZONTAL"?'Y':'X')){if(r.count<0||coordinates+size_t(r.count)>16000000)throw std::runtime_error("Physical Track table exceeds explicit 128MiB budget");coordinates+=size_t(r.count);for(int k=0;k<r.count;k++)t.push_back(r.start+k*r.step);}std::sort(t.begin(),t.end());t.erase(std::unique(t.begin(),t.end()),t.end());const auto&a=layer.direction=="HORIZONTAL"?grid.ys:grid.xs;auto&c=capacities[l];for(size_t k=0;k+1<a.size();k++){auto first=std::lower_bound(t.begin(),t.end(),a[k]),last=k+2==a.size()?std::upper_bound(t.begin(),t.end(),a[k+1]):std::lower_bound(t.begin(),t.end(),a[k+1]);c.push_back(int(last-first));}}
    }
    int capacity(int x,int y,int l)const{return capacities[l].at(grid.d.layers.at(l).direction=="HORIZONTAL"?y:x);}
    template<class F> void build(F output,const std::function<void()>&poll=[](){},size_t workspace=32*1024*1024){
        // Rectangles are spooled to layer/row stripes, not retained as millions
        // of cell intervals. Each stripe unions actual Track bits in-place.
        struct OwnedDirectory {std::filesystem::path p;~OwnedDirectory(){if(!p.empty()){std::error_code e;std::filesystem::remove_all(p,e);}}} temp;
        std::random_device random;for(int tries=0;tries<100;tries++){auto candidate=std::filesystem::temp_directory_path()/("gr_physical_"+std::to_string(random())+"_"+std::to_string(random()));if(std::filesystem::create_directory(candidate)){temp.p=candidate;break;}if(tries==99)throw std::runtime_error("Cannot create physical workspace");}
        int maxcap=0;for(auto&c:capacities)for(int n:c)maxcap=std::max(maxcap,n);size_t words=std::max<size_t>(1,(size_t(maxcap)+63)/64),rowbytes=size_t(grid.nx)*words*8;
        if(rowbytes>workspace)throw std::runtime_error("One physical stripe row exceeds workspace budget");
        int height=std::max(1,std::min(64,int(workspace/rowbytes)));
        struct Record {Rect r;bool pin;};std::map<std::pair<int,int>,std::ofstream> streams;std::set<std::pair<int,int>> buckets;
        auto filename=[&](std::pair<int,int> key){return temp.p/(std::to_string(key.first)+"_"+std::to_string(key.second)+".bin");};
        physical_rectangles(grid.d,[&](Rect r,bool pin){
            if(++rectangles%4096==0)poll();int lo=grid.ny,hi=-1;
            grid.cells(r,[&](Id v,int,int y,Rect){blocked[size_t(v%grid.plane)]=1;lo=std::min(lo,y);hi=std::max(hi,y);++intervals;});
            if(hi<lo)return;
            for(int band=lo/height;band<=hi/height;band++){
                std::pair<int,int> key{r.layer,band};buckets.insert(key);auto it=streams.find(key);
                if(it==streams.end()){if(streams.size()>=64)streams.erase(streams.begin());auto&f=streams[key];f.open(filename(key),std::ios::binary|std::ios::app);f.exceptions(std::ios::badbit|std::ios::failbit);it=streams.find(key);}
                Record record{};record.r=r;record.pin=pin;it->second.write(reinterpret_cast<const char*>(&record),sizeof(record));spool_bytes+=sizeof(record);
            }
        });streams.clear();
        for(auto key:buckets){poll();int l=key.first,start=key.second*height,rows=std::min(height,grid.ny-start);size_t count=size_t(rows)*grid.nx;std::vector<std::uint64_t> bits(count*words,0);max_workspace_bytes=std::max(max_workspace_bytes,bits.size()*8);
            const auto&t=tracks[l];bool horizontal=grid.d.layers.at(l).direction=="HORIZONTAL";
            std::ifstream in(filename(key),std::ios::binary);if(!in)throw std::runtime_error("Missing physical stripe");Record record{};size_t reads=0;
            while(in.read(reinterpret_cast<char*>(&record),sizeof(record))){if(++reads%4096==0)poll();Rect r=record.r;r.y1=std::max(r.y1,grid.ys[start]);r.y2=std::min(r.y2,grid.ys[start+rows]);if(r.y1>r.y2)continue;
                grid.cells(r,[&](Id,int x,int y,Rect q){if(y<start||y>=start+rows)return;double clo=horizontal?grid.ys[y]:grid.xs[x],chi=horizontal?grid.ys[y+1]:grid.xs[x+1],lo=horizontal?q.y1:q.x1,hi=horizontal?q.y2:q.x2;
                    auto cb=std::lower_bound(t.begin(),t.end(),clo),ce=chi==(horizontal?grid.ys.back():grid.xs.back())?std::upper_bound(t.begin(),t.end(),chi):std::lower_bound(t.begin(),t.end(),chi);
                    auto a=std::max(cb,std::lower_bound(t.begin(),t.end(),lo)),b=std::min(ce,std::upper_bound(t.begin(),t.end(),hi));auto base=(size_t(y-start)*grid.nx+x)*words;
                    auto mark=[&](int k){bits[base+size_t(k)/64]|=std::uint64_t(1)<<(k%64);};
                    if(a<b)for(auto it=a;it!=b;++it)mark(int(it-cb));else if(record.pin){++min_pin_shapes;if(cb==ce)mark(0);else{mark(no_track_pin_index(cb,ce,(lo+hi)/2,pin_policy));}}
                });
            }if(!in.eof())throw std::runtime_error("Physical stripe read failed");
            for(int y=start;y<start+rows;y++)for(int x=0;x<grid.nx;x++){size_t base=(size_t(y-start)*grid.nx+x)*words;int n=0;for(size_t w=0;w<words;w++){auto value=bits[base+w];while(value){value&=value-1;++n;}}if(n){++fixed_cells;output(grid.id(x,y,l),capacity(x,y,l),n);}}
        }
    }
};
}
