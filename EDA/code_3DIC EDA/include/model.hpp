#pragma once
#include "json.hpp"
#include <cstdint>
#include <set>
#include <unordered_map>
#include <filesystem>
#include <chrono>
#include <functional>
namespace gr {
using Id=std::uint64_t;
struct Rect {double x1=0,y1=0,x2=0,y2=0;int layer=0;};
struct Layer {std::string name,type,direction;double width=0,spacing=0,pitch=0;};
struct Macro {double width=0,height=0,ox=0,oy=0;std::map<std::string,std::vector<Rect>> pins;std::vector<Rect> obs;};
struct Instance {std::string name,macro,orient;double x=0,y=0;};
struct Pin {std::string name;double x=0,y=0;int layer=0;};
struct Net {std::string name;std::vector<Pin> pins;bool cross=false;};
struct Track {char axis;double start,step;int count,layer;};
struct Design {
    std::string version,name;double dbu=0;Rect die;
    std::map<int,Layer> layers;std::map<std::string,Macro> macros;
    std::map<std::string,Instance> instances;std::vector<Net> nets;
    std::vector<Track> tracks;std::map<char,std::vector<double>> grid;
    std::set<std::pair<int,int>> vias;bool tsv_cut=false;
    // Retain full placed DEF IO geometry for physical-rule audits. Access
    // points and all historical parser/routing semantics remain unchanged.
    std::map<std::string,std::vector<Rect>> io_pin_rects;
};
struct Edge {Id a,b;bool operator<(const Edge&e)const{return a<e.a||(a==e.a&&b<e.b);}};
struct Route {std::set<Id> nodes;std::set<Edge> edges;std::vector<Rect> guides;Id tsv=0;bool cross=false;std::vector<Id> steiner_points;
    // V3.4 transaction state: moved/backed up together with the complete route.
    std::vector<Id> demand_exempt;std::vector<std::pair<Id,int>> tsv_demand,resource_demand;
    std::vector<Id> pin_access_nodes; // V3.5: one real geometry access per terminal.
};
struct Config {double wire=1,via=0.5,tsv=4,congestion=4,history=1,thermal=1,time_limit=600;int iterations=2,stagnation=2,margin=8;std::size_t search_limit=1500000,trace_mb=32;
    // All zero preserves V2 routing. Numeric modes are validated by main.cpp.
    int net_order_mode=0,tree_route_mode=0,reroute_priority_mode=0;
    int congestion_cost_mode=0,reroute_policy_mode=0,reroute_max_nets=512,congestion_scope_mode=0;
    double reroute_pass_seconds=20,reroute_wire_growth=0.005;
    bool v3_final=false; // Selected by the formal build, not a JSON option.
    int v3_stage=0; // Internal ablation: 1 Steiner, 2 negotiated, 3 global TSV.
    bool v3_batch=false; // Numeric V3.2 build; not a JSON parameter.
    bool v3_calibrated=false; // V3.3 only; selected by the build, never JSON.
    bool v3_rules_20261029=false; // V3.4 physical announcement model, NOT an official scorer.
    bool v3_geometry_bounded=false; // V3.5 build only; same public JSON parameters.
};
std::string read_file(const std::filesystem::path& p);
void write_json(const std::filesystem::path&p,const Json&j);
// Write a complete temporary file, then publish it; never reread the JSON.
void write_json(const std::filesystem::path&p,const std::function<void(std::ostream&)>&writer);
int layer_number(const std::string& s);
std::pair<double,double> orient_point(double x,double y,double w,double h,const std::string& o);
Rect placed_rect(const Rect&r,const Macro&m,const Instance&i,double dbu);
void parse_lef(const std::filesystem::path&,Design&);
void parse_def(const std::filesystem::path&,Design&);
struct Trace {
    std::ofstream* out=nullptr;std::size_t bytes=0,limit=0;bool truncated=false;int seq=0;
    Trace(const std::string&path,std::size_t mb);~Trace();
    void event(std::string type,Json data=Json::object());
};
}
