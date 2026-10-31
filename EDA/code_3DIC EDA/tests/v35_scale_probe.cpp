// Resource-only probe. Deliberately never calls Router::run/route/search.
#include "router.hpp"
#include <iostream>
#include <sys/resource.h>
using namespace gr;
namespace gr {struct RouterTestAccess {static Json state(const Router&r){Json j=Json::object();j["thermal_ready"]=r.v3_thermal_ready_;j["thermal_cells"]=double(r.v3_thermal_rise_.size());j["warm_sites"]=double(r.v35_warm_sites_.size());return j;}};}
int main(int argc,char**argv){try{if(argc!=4)throw std::runtime_error("probe lef def thermal");rlimit limit{3ULL*1024*1024*1024,3ULL*1024*1024*1024};if(setrlimit(RLIMIT_AS,&limit))throw std::runtime_error("Cannot set 3GiB probe limit");auto start=std::chrono::steady_clock::now();Design d;parse_lef(argv[1],d);parse_def(argv[2],d);Config c;c.time_limit=180;Trace trace("",0);Router router(d,c,trace);router.load_thermal(argv[3]);router.build_fixed_demand();auto j=RouterTestAccess::state(router);j["rules"]=router.rules_20261029_summary();j["nets"]=double(d.nets.size());j["resources"]=double(router.resources.size());j["elapsed_seconds"]=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();rusage ru{};getrusage(RUSAGE_SELF,&ru);j["peak_rss_bytes"]=double(ru.ru_maxrss)*1024;j["full_routing_executed"]=false;std::cout<<j.dump()<<'\n';return 0;}catch(const std::exception&e){std::cerr<<"RESOURCE_PROBE_INCOMPLETE: "<<e.what()<<'\n';return 2;}}
