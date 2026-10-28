#include "router.hpp"
#include <iostream>
#include <fstream>
#include <set>
#include <memory>
#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#endif
int main(int argc,char**argv){
    using namespace gr;
    Progress progress;
    try{
        auto program_started=std::chrono::steady_clock::now();
        std::vector<std::string> args;
#ifdef _WIN32
        (void)argc;(void)argv;
        int n=0;LPWSTR* wide=CommandLineToArgvW(GetCommandLineW(),&n);for(int i=0;i<n;i++){int len=WideCharToMultiByte(CP_UTF8,0,wide[i],-1,nullptr,0,nullptr,nullptr);std::string s(len,'\0');WideCharToMultiByte(CP_UTF8,0,wide[i],-1,s.data(),len,nullptr,nullptr);s.pop_back();args.push_back(s);}LocalFree(wide);
#else
        for(int i=0;i<argc;i++)args.emplace_back(argv[i]);
#endif
        for(size_t i=1;i+1<args.size();i++)if(args[i]=="--progress-dir")progress.open(std::filesystem::u8path(args[i+1]));
        Progress::Scope run_log(&progress,"RUN");
        std::map<std::string,std::string> opt;Config c;std::filesystem::path output;
        {Progress::Scope arguments_log(&progress,"ARGUMENTS");
        std::set<std::string> allowed{"--def","--lef","--thermal-report","--output","--metrics","--trace","--config","--time-limit","--iterations","--trace-limit-mb","--progress-dir","--progress-every","--monitor-interval","--heartbeat-interval"};
        for(size_t i=1;i<args.size();i++){if(args[i]=="--help"){std::cout<<"router --def input.def --lef input.lef --thermal-report thermal.json --output output.guide [--metrics result.json] [--trace trace.jsonl] [--config config.json] [--time-limit seconds] [--iterations N] [--progress-dir directory] [--progress-every 1000] [--monitor-interval 5] [--heartbeat-interval 10]\n";return 0;}if(!allowed.count(args[i])||i+1>=args.size())throw std::runtime_error("Unknown/missing CLI option: "+args[i]);auto key=args[i];if(opt.count(key))throw std::runtime_error("Duplicate CLI option: "+key);opt[key]=args[++i];}
        for(auto k:{"--def","--lef","--thermal-report","--output"})if(!opt.count(k))throw std::runtime_error(std::string("Required option: ")+k);
        output=std::filesystem::u8path(opt.at("--output"));
        for(auto k:{"--def","--lef","--thermal-report"})if(std::filesystem::weakly_canonical(output)==std::filesystem::weakly_canonical(std::filesystem::u8path(opt.at(k))))throw std::runtime_error("Output aliases an input");
        if(output.extension()!=".guide")throw std::runtime_error("Output must have .guide extension");
        if(opt.count("--config")){auto j=Json::parse(read_file(std::filesystem::u8path(opt["--config"])));for(auto&[k,v]:j.obj){double n=v.number();if(k=="wire")c.wire=n;else if(k=="via")c.via=n;else if(k=="tsv")c.tsv=n;else if(k=="congestion")c.congestion=n;else if(k=="history")c.history=n;else if(k=="thermal")c.thermal=n;else if(k=="iterations")c.iterations=int(n);else if(k=="stagnation")c.stagnation=int(n);else if(k=="margin")c.margin=int(n);else if(k=="search_limit")c.search_limit=size_t(n);else if(k=="time_limit")c.time_limit=n;else if(k=="trace_mb")c.trace_mb=size_t(n);else throw std::runtime_error("Unknown config key: "+k);}}
        if(opt.count("--time-limit"))c.time_limit=std::stod(opt["--time-limit"]);
        if(opt.count("--iterations"))c.iterations=std::stoi(opt["--iterations"]);
        if(opt.count("--trace-limit-mb"))c.trace_mb=std::stoull(opt["--trace-limit-mb"]);
        if(!(c.wire>0&&c.via>0&&c.tsv>=0&&c.congestion>=0&&c.history>=0&&c.thermal>=0&&c.time_limit>0&&c.iterations>=0&&c.iterations<=100&&c.stagnation>0&&c.margin>0&&c.search_limit>0&&c.trace_mb>0))throw std::runtime_error("Invalid router configuration");
        size_t progress_every=opt.count("--progress-every")?std::stoull(opt["--progress-every"]):1000;
        double monitor_interval=opt.count("--monitor-interval")?std::stod(opt["--monitor-interval"]):5,heartbeat_interval=opt.count("--heartbeat-interval")?std::stod(opt["--heartbeat-interval"]):10;
        if(!progress_every||!std::isfinite(monitor_interval)||monitor_interval<=0||!std::isfinite(heartbeat_interval)||heartbeat_interval<=0)throw std::runtime_error("Invalid monitoring interval");
        progress.configure(progress_every,monitor_interval,heartbeat_interval);
        // Debug outputs may never overwrite inputs or each other.
        std::set<std::filesystem::path> paths;for(auto k:{"--def","--lef","--thermal-report","--output","--metrics","--trace","--config"})if(opt.count(k)){auto p=std::filesystem::weakly_canonical(std::filesystem::u8path(opt[k]));if(!paths.insert(p).second)throw std::runtime_error("CLI file paths must be distinct");}
        } // ARGUMENTS completed
        Trace trace(opt.count("--trace")?opt["--trace"]:"",c.trace_mb);trace.event("parse_started");Design d;
        {Progress::Scope stage(&progress,"PARSE_LEF");parse_lef(std::filesystem::u8path(opt["--lef"]),d);}
        {Progress::Scope stage(&progress,"PARSE_DEF");parse_def(std::filesystem::u8path(opt["--def"]),d);}
        trace.event("parse_complete");
        std::unique_ptr<Router> instance;
        {Progress::Scope stage(&progress,"GRAPH_BUILD");instance=std::make_unique<Router>(d,c,trace,&progress);}
        Router& router=*instance;router.started=program_started;router.check_time();
        {Progress::Scope stage(&progress,"THERMAL");router.load_thermal(std::filesystem::u8path(opt["--thermal-report"]));}
        {Progress::Scope stage(&progress,"FIXED_DEMAND");router.build_fixed_demand();}
        router.run();
        if(opt.count("--metrics")){Progress::Scope stage(&progress,"WRITE_RESULT");router.write_result(std::filesystem::u8path(opt["--metrics"]));}
        {Progress::Scope stage(&progress,"WRITE_GUIDE");router.write_guide(output);}
        run_log.finish();progress.success();
        std::cout<<"ROUTED nets="<<d.nets.size()<<" overflow_local="<<router.overflow()<<" runtime_seconds="<<router.elapsed()<<'\n';return 0;
    }catch(const RouterTimeout&e){progress.failure(e.what(),"time_budget");std::cerr<<"ERROR: "<<e.what()<<std::endl;return 1;}
    catch(const std::bad_alloc&e){progress.failure(e.what(),"allocation_failure");std::cerr<<"ERROR: "<<e.what()<<std::endl;return 1;}
    catch(const std::exception&e){progress.failure(e.what(),"router_exception");std::cerr<<"ERROR: "<<e.what()<<std::endl;return 1;}
}
