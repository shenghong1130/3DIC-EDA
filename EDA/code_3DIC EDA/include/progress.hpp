#pragma once
#include "json.hpp"
#include <fstream>
#include <filesystem>
#include <chrono>
#include <iostream>
#include <exception>
#include <optional>
#include <cstdlib>
#include <string_view>
#ifdef __linux__
#include <unistd.h>
#endif
namespace gr {
// Optional cooperative diagnostics; no threads and no routing decisions.
class Progress {
    using Clock=std::chrono::steady_clock;
    struct Frame {const char* phase="STARTUP";Clock::time_point began;bool report=false,fine=false;};
    Clock::time_point began_=Clock::now(),last_heartbeat_=began_,last_memory_=began_;
    std::ofstream human_,events_,memory_;
    const char* phase_="STARTUP";const char* error_phase_=nullptr;
    std::string net_,error_net_;size_t index_=0,total_=0,completed_=0,error_index_=0,every_=1000;
    int iteration_=0;double interval_=5,heartbeat_=10,stage_time_=0;
    bool enabled_=false,detail_=true,report_net_=true,external_=false;
    std::optional<double> rss_,peak_,available_,swap_;
    std::string output_section_;size_t output_completed_=0,output_total_=0;
    double output_bytes_=0,output_seconds_=0,memory_sample_seconds_=0;
    void sample(bool force=false) noexcept {
        if(!enabled_||(external_&&!force))return;
        try {
            auto now=Clock::now();if(!force&&std::chrono::duration<double>(now-last_memory_).count()<interval_&&sampled_)return;
            last_memory_=now;sampled_=true;memory_sample_seconds_=elapsed();
#ifdef __linux__
            std::string line;std::ifstream status("/proc/self/status");
            while(std::getline(status,line)){std::istringstream in(line);std::string key;double kb;if(in>>key>>kb){if(key=="VmRSS:")rss_=kb*1024;else if(key=="VmHWM:")peak_=kb*1024;}}
            std::ifstream mem("/proc/meminfo");
            while(std::getline(mem,line)){std::istringstream in(line);std::string key;double kb;if(in>>key>>kb){if(key=="MemAvailable:")available_=kb*1024;else if(key=="SwapFree:")swap_=kb*1024;}}
#endif
            auto value=[](auto v){return v?std::to_string(*v):"N/A";};
            if(memory_){memory_<<elapsed()<<','<<pid()<<','<<value(rss_)<<','<<value(peak_)<<','<<value(available_)<<','<<value(swap_)<<'\n';memory_.flush();}
        }catch(...){}
    }
    bool sampled_=false;
    Frame enter(const char*phase,bool fine) noexcept {
        bool report=!fine||detail_||(report_net_&&std::string_view(phase)=="RIP_UP_REROUTE");
        Frame f{phase_,report?Clock::now():Clock::time_point{},report,fine};phase_=phase;
        if(std::string_view(phase)=="WRITE_RESULT"){output_section_.clear();output_completed_=output_total_=0;output_bytes_=output_seconds_=0;sample(true);}
        if(f.report)emit("phase_started","Started");
        return f;
    }
    void leave(const Frame&f,bool failed) noexcept {
        try {
            if(failed&&!error_phase_){error_phase_=phase_;error_net_=net_;error_index_=index_;}
            stage_time_=f.report?std::chrono::duration<double>(Clock::now()-f.began).count():0;
            if(f.report||(failed&&!f.fine))emit(failed?"phase_failed":"phase_completed",failed?"Failed while unwinding":"Completed");
            phase_=f.phase;stage_time_=0;
        }catch(...){}
    }
public:
    class Scope {
        Progress* log_;Frame frame_;int exceptions_;
    public:
        Scope(Progress*log,const char*phase,bool fine=false) noexcept:log_(log&&log->enabled_?log:nullptr),exceptions_(std::uncaught_exceptions()){if(log_)frame_=log_->enter(phase,fine);}
        ~Scope(){if(log_)log_->leave(frame_,std::uncaught_exceptions()>exceptions_);}
        void finish() noexcept {if(log_){log_->leave(frame_,false);log_=nullptr;}}
        Scope(const Scope&)=delete;Scope&operator=(const Scope&)=delete;
    };
    static long pid(){
#ifdef __linux__
        return long(getpid());
#else
        return 0;
#endif
    }
    double elapsed()const{return std::chrono::duration<double>(Clock::now()-began_).count();}
    void open(const std::filesystem::path&dir) noexcept {
        try {
            std::filesystem::create_directories(dir);
            for(auto name:{"progress.log","progress.jsonl"})if(std::filesystem::exists(dir/name))throw std::runtime_error("Progress file already exists");
            external_=std::getenv("GR_EXTERNAL_RESOURCE_MONITOR")!=nullptr;
            if(!external_&&std::filesystem::exists(dir/"resource_monitor.csv"))throw std::runtime_error("Resource log already exists");
            human_.open(dir/"progress.log",std::ios::app);events_.open(dir/"progress.jsonl",std::ios::app);
            if(!human_||!events_)throw std::runtime_error("Cannot open progress logs");
            if(!external_){memory_.open(dir/"resource_monitor.csv",std::ios::app);memory_<<"elapsed_seconds,pid,rss_bytes,router_peak_rss_bytes,mem_available_bytes,swap_free_bytes\n";memory_.flush();}
            enabled_=true;emit("startup","Router started");
        }catch(const std::exception&e){enabled_=false;std::cerr<<"MONITOR_UNAVAILABLE: "<<e.what()<<std::endl;}
    }
    void configure(size_t every,double interval,double heartbeat){every_=every;interval_=interval;heartbeat_=heartbeat;}
    void emit(const char*type,std::string_view message="",const char*kind="") noexcept {
        if(!enabled_)return;
        try {
            sample();Json j=Json::object();j["event"]=type;j["phase"]=phase_;j["routing_mode"]=iteration_?"OPTIMIZATION":"INITIAL_ROUTING";j["net"]=net_;
            j["net_index"]=double(index_);j["total_nets"]=double(total_);j["completed_nets"]=double(completed_);j["iteration"]=iteration_;
            j["elapsed_seconds"]=elapsed();j["stage_seconds"]=stage_time_;j["message"]=std::string(message);j["error_kind"]=kind;
            j["progress_percent"]=total_?Json(100.0*completed_/total_):Json();j["pid"]=pid()?Json(double(pid())):Json();
            j["resource_source"]=external_?(sampled_?"proc_self_output_checkpoint":"external_supervisor"):"cooperative_proc_self";
            if(sampled_)j["resource_sample_elapsed_seconds"]=memory_sample_seconds_;
            if(std::string_view(phase_)=="WRITE_RESULT"){
                j["progress_kind"]="output";j["output_section"]=output_section_;j["output_completed"]=double(output_completed_);j["output_total"]=double(output_total_);
                j["output_bytes"]=output_bytes_;j["output_elapsed_seconds"]=output_seconds_;
            }
            auto field=[&](const char*k,auto v){j[k]=v?Json(*v):Json();};
            field("rss_bytes",rss_);field("router_peak_rss_bytes",peak_);field("mem_available_bytes",available_);field("swap_free_bytes",swap_);
            events_<<j.dump()<<'\n';events_.flush();
            human_<<'['<<type<<"] ["<<elapsed()<<"s] ["<<phase_<<"] "<<message<<" | phase="<<(iteration_?"OPTIMIZATION":"INITIAL_ROUTING")
                  <<" iteration="<<iteration_<<" net="<<index_<<'/'<<total_<<" name="<<net_<<" completed="<<completed_
                  <<" percent="<<(total_?100.0*completed_/total_:0)<<" stage_seconds="<<stage_time_<<'\n';human_.flush();
        }catch(...){}
    }
    void poll() noexcept {if(!enabled_)return;auto now=Clock::now();if(std::chrono::duration<double>(now-last_heartbeat_).count()<heartbeat_)return;last_heartbeat_=now;emit("heartbeat","Execution checkpoint; not a completed Net");}
    void output(const char*section,size_t completed,size_t total,double bytes,double seconds,const char*message="") noexcept {
        if(!enabled_)return;
        try{
            output_section_=section;output_completed_=completed;output_total_=total;output_bytes_=bytes;output_seconds_=seconds;
            sample(true);
            emit("output_progress",std::string(section)+" "+std::to_string(completed)+"/"+std::to_string(total)+" "+message+" bytes="+std::to_string(bytes)+" output_seconds="+std::to_string(seconds));
        }catch(...){}
    }
    void pass(size_t total,int iteration) noexcept {if(!enabled_)return;total_=total;iteration_=iteration;index_=completed_=0;net_.clear();report_net_=detail_=false;error_phase_=nullptr;}
    void net(size_t index,size_t total,const std::string&name,int iteration) noexcept {
        if(!enabled_)return;
        try {index_=index;total_=total;net_=name;iteration_=iteration;completed_=index?index-1:0;report_net_=index==1||index==total||index%every_==0;
            // Initial routing shows fine stages at both ends. Later passes keep
            // Net/reroute summaries, with nested details only every N positions.
            detail_=report_net_&&(iteration==0||index%every_==0);
            if(index==1)emit("net_started","First Net / scan position in pass");
            poll();
        }catch(...){}
    }
    void completed() noexcept {if(!enabled_)return;completed_=index_;if(report_net_)emit("progress","Completed scan position; optimization may skip non-hot Nets");poll();}
    void failure(std::string_view message,const char*kind) noexcept {try{if(error_phase_){phase_=error_phase_;net_=error_net_;index_=error_index_;}emit("error",message,kind);}catch(...){}}
    void success() noexcept {phase_="COMPLETE";emit("complete","Router completed successfully");}
};
}
