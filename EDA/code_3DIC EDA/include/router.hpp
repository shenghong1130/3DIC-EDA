#pragma once
#include "model.hpp"
#include "progress.hpp"
#include <functional>
#include <array>
namespace gr {
struct RouterTimeout : std::runtime_error {RouterTimeout():std::runtime_error("Router time budget exceeded") {}};
struct Resource {int capacity=0,usage=0,fixed=0;double history=0;};
class Router {
public:
    Design& d;Config cfg;Trace& trace;std::vector<double> xs,ys,heat,thermal_count;
    int nx=0,ny=0;double ambient=25,unit=1;std::unordered_map<Id,Resource> resources;
    std::vector<Route> routes;std::vector<Json> rounds;std::chrono::steady_clock::time_point started;
    Progress* progress=nullptr;
#ifdef GR_PROFILE
    // Build-only diagnostics, written to GR_PROFILE_PATH, never result.json.
    struct Profile {
        std::uint64_t astar_calls=0,expansions=0,max_expansions=0,queue_pops=0,stale_pops=0,max_dist_nodes=0;
        std::uint64_t sparse_attempts=0,dense_attempts=0,max_workspace_bytes=0,margin_attempts=0;
        std::uint64_t capacity_calls=0,capacity_cache_hits=0,resource_insertions=0,tsv_calls=0,tsv_candidates=0,heat_values_scanned=0;
        std::uint64_t rerouted_nets=0,route_copy_count=0,route_move_count=0,rollback_nets=0,route_json_calls=0;
        double astar_seconds=0,tsv_seconds=0,heat_scan_seconds=0,backup_seconds=0,rollback_seconds=0,reroute_seconds=0;
    } mutable profile;
    ~Router() noexcept;
#endif
    Router(Design&,Config,Trace&,Progress* = nullptr);
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
    void result_json(std::ostream&)const;
    void write_result(const std::filesystem::path&)const;
    void write_guide(const std::filesystem::path&)const;
private:
    struct SearchInfo {double g=0;Id parent=0;};
    struct SearchCell {SearchInfo info;std::uint32_t generation=0;};
    // At most 8192 local states (~192 KiB), never nx*ny*20. Larger
    // windows keep the sparse map. Buffers survive searches, not labels.
    static constexpr size_t dense_limit_=8192;
    std::vector<SearchCell> search_cells_;
    std::uint32_t search_generation_=0;
    std::vector<double> x_centers_,y_centers_;
    std::array<bool,21> layer_present_{},layer_horizontal_{},via_up_{},via_down_{};
    std::array<std::vector<int>,21> axis_capacity_;
    bool static_frozen_=false;
    double heat_min_=25,heat_max_=25;
    void refresh_layers();
    void refresh_heat_stats();
    void refresh_capacities();
    std::pair<long long,int> reroute_pass(int iteration,long long best);
    friend struct RouterTestAccess;
};
}
