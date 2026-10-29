// Read-only small-case calibration utility. Compile to a temporary directory.
#include "router.hpp"
#include <iostream>
int main(int argc,char**argv){using namespace gr;try{
    if(argc!=4)throw std::runtime_error("quality_audit input.lef input.def result.json");
    if(std::filesystem::file_size(argv[3])>128*1024*1024)throw std::runtime_error("Audit limited to small result files (128 MiB)");
    Design d;parse_lef(argv[1],d);parse_def(argv[2],d);Config c;c.time_limit=180;Trace t("",1);Router r(d,c,t);r.build_fixed_demand();r.build_guide_model();
    Json data=Json::parse(read_file(argv[3]));double total=0;int unknown=0;Json out=Json::object(),nets=Json::array();
    for(const auto&net:data.at("nets").arr){Route route;for(const auto&v:net.at("guides").arr)route.guides.push_back({v.arr[0].number(),v.arr[1].number(),v.arr[2].number(),v.arr[3].number(),int(v.arr[4].number())});
        double estimate=r.guide_wirelength(route);Json n=Json::object();n["net"]=net.at("net");n["estimate_dbu"]=std::isfinite(estimate)?Json(estimate):Json();n["graph_wirelength"]=net.at("wirelength_dbu_local");nets.push(n);if(std::isfinite(estimate))total+=estimate;else ++unknown;
    }
    Json resources=Json::array();for(const auto&row:data.at("resources").arr){Id v=Id(row.arr[0].number());Json item=row;item.push(r.guide_fixed_demand(v));resources.push(item);}
    out["guide_estimate_sum"]=total;out["unknown_nets"]=unknown;out["nets"]=nets;out["resources_with_union_fixed"]=resources;out.write(std::cout);std::cout<<'\n';return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
