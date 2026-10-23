#pragma once
#include "model.hpp"
#include <functional>
namespace gr {
struct Resource {int capacity=0,usage=0,fixed=0;double history=0;};
class Router {
public:
    Design& d;Config cfg;Trace& trace;std::vector<double> xs,ys,heat,thermal_count;
    int nx=0,ny=0;double ambient=25,unit=1;std::unordered_map<Id,Resource> resources;
    std::vector<Route> routes;std::vector<Json> rounds;std::chrono::steady_clock::time_point started;
    Router(Design&,Config,Trace&);
    Id id(int x,int y,int l)const{return (Id(l)*ny+y)*nx+x;}
    void decode(Id v,int&x,int&y,int&l)const{x=int(v%nx);v/=nx;y=int(v%ny);l=int(v/ny);}
    int locate(const std::vector<double>& lines,double x)const;
    Id pin_node(const Pin&p)const;
    Rect cell(Id v)const;
    std::pair<double,double> center(Id v)const;
    Resource& resource(Id);
    int capacity(Id)const;
    double elapsed()const;
    void check_time()const;
    void load_thermal(const std::filesystem::path&);
    void build_fixed_demand();
    std::vector<Id> search(Id,Id,int,int,bool);
    std::vector<std::pair<int,int>> mst(const std::vector<Id>&)const;
    Id select_tsv(const Net&);
    Route route(const Net&);
    void occupy(const Route&,int);
    long long overflow()const;
    double wirelength(const Route&)const;
    void run();
    void make_guides(Route&)const;
    void validate(const Net&,const Route&)const;
    Json route_json(const Net&,const Route&)const;
    Json result_json()const;
    void write_guide(const std::filesystem::path&)const;
};
}
