#include "router.hpp"
#include <iostream>
#include <fstream>
#include <set>
#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#endif
int main(int argc,char**argv){
    using namespace gr;
    try{
        auto program_started=std::chrono::steady_clock::now();
        std::vector<std::string> args;
#ifdef _WIN32
        int n=0;LPWSTR* wide=CommandLineToArgvW(GetCommandLineW(),&n);for(int i=0;i<n;i++){int len=WideCharToMultiByte(CP_UTF8,0,wide[i],-1,nullptr,0,nullptr,nullptr);std::string s(len,'\0');WideCharToMultiByte(CP_UTF8,0,wide[i],-1,s.data(),len,nullptr,nullptr);s.pop_back();args.push_back(s);}LocalFree(wide);
#else
        for(int i=0;i<argc;i++)args.emplace_back(argv[i]);
#endif
        std::map<std::string,std::string> opt;std::set<std::string> allowed{"--def","--lef","--thermal-report","--output","--metrics","--trace","--config","--time-limit","--iterations","--trace-limit-mb"};
        for(size_t i=1;i<args.size();i++){if(args[i]=="--help"){std::cout<<"router --def input.def --lef input.lef --thermal-report thermal.json --output output.guide [--metrics result.json] [--trace trace.jsonl] [--config config.json] [--time-limit seconds] [--iterations N]\n";return 0;}if(!allowed.count(args[i])||i+1>=args.size())throw std::runtime_error("Unknown/missing CLI option: "+args[i]);auto key=args[i];if(opt.count(key))throw std::runtime_error("Duplicate CLI option: "+key);opt[key]=args[++i];}
        for(auto k:{"--def","--lef","--thermal-report","--output"})if(!opt.count(k))throw std::runtime_error(std::string("Required option: ")+k);
        auto output=std::filesystem::u8path(opt.at("--output"));
        for(auto k:{"--def","--lef","--thermal-report"})if(std::filesystem::weakly_canonical(output)==std::filesystem::weakly_canonical(std::filesystem::u8path(opt.at(k))))throw std::runtime_error("Output aliases an input");
        if(output.extension()!=".guide")throw std::runtime_error("Output must have .guide extension");
        Config c;if(opt.count("--config")){auto j=Json::parse(read_file(std::filesystem::u8path(opt["--config"])));for(auto&[k,v]:j.obj){double n=v.number();if(k=="wire")c.wire=n;else if(k=="via")c.via=n;else if(k=="tsv")c.tsv=n;else if(k=="congestion")c.congestion=n;else if(k=="history")c.history=n;else if(k=="thermal")c.thermal=n;else if(k=="iterations")c.iterations=int(n);else if(k=="stagnation")c.stagnation=int(n);else if(k=="margin")c.margin=int(n);else if(k=="search_limit")c.search_limit=size_t(n);else if(k=="time_limit")c.time_limit=n;else if(k=="trace_mb")c.trace_mb=size_t(n);else throw std::runtime_error("Unknown config key: "+k);}}
        if(opt.count("--time-limit"))c.time_limit=std::stod(opt["--time-limit"]);
        if(opt.count("--iterations"))c.iterations=std::stoi(opt["--iterations"]);
        if(opt.count("--trace-limit-mb"))c.trace_mb=std::stoull(opt["--trace-limit-mb"]);
        if(!(c.wire>0&&c.via>0&&c.tsv>=0&&c.congestion>=0&&c.history>=0&&c.thermal>=0&&c.time_limit>0&&c.iterations>=0&&c.iterations<=100&&c.stagnation>0&&c.margin>0&&c.search_limit>0&&c.trace_mb>0))throw std::runtime_error("Invalid router configuration");
        // Debug outputs may never overwrite inputs or each other.
        std::set<std::filesystem::path> paths;for(auto k:{"--def","--lef","--thermal-report","--output","--metrics","--trace","--config"})if(opt.count(k)){auto p=std::filesystem::weakly_canonical(std::filesystem::u8path(opt[k]));if(!paths.insert(p).second)throw std::runtime_error("CLI file paths must be distinct");}
        Trace trace(opt.count("--trace")?opt["--trace"]:"",c.trace_mb);trace.event("parse_started");Design d;parse_lef(std::filesystem::u8path(opt["--lef"]),d);parse_def(std::filesystem::u8path(opt["--def"]),d);trace.event("parse_complete");
        Router router(d,c,trace);router.started=program_started;router.check_time();router.load_thermal(std::filesystem::u8path(opt["--thermal-report"]));router.build_fixed_demand();router.run();
        if(opt.count("--metrics"))write_json(std::filesystem::u8path(opt["--metrics"]),router.result_json());
        router.write_guide(output);
        std::cout<<"ROUTED nets="<<d.nets.size()<<" overflow_local="<<router.overflow()<<" runtime_seconds="<<router.elapsed()<<'\n';return 0;
    }catch(const std::exception&e){std::cerr<<"ERROR: "<<e.what()<<'\n';return 1;}
}
