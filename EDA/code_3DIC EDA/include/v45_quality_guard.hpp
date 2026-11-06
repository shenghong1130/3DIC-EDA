#pragma once
#include <cmath>
#include <algorithm>
#include <map>
#include <vector>
#include <stdexcept>
namespace gr::v45 {
// Joint updates aggregate coincident bins BEFORE squaring. Independent move
// deltas cannot be added when multiple TSVs touch the same thermal bin.
class Thermal {
    const std::vector<double>& h;
    std::vector<int> d;
    double sh=0,hh=0,sd=0,dd=0,hd=0;
    double rho(double a,double b)const {
        double n=double(d.size()),vh=hh-sh*sh/n,vd=a-sd*sd/n;
        return vh>1e-20&&vd>1e-20?std::clamp((b-sh*sd/n)/std::sqrt(vh*vd),-1.,1.):0;
    }
public:
    using Moves=std::vector<std::pair<size_t,size_t>>;
    struct Update {std::map<size_t,int> delta;double dd=0,hd=0;};
    Thermal(const std::vector<double>& heat,std::vector<int> counts):h(heat),d(std::move(counts)) {
        if(h.empty()||h.size()!=d.size())throw std::runtime_error("V45 thermal grid mismatch");
        for(size_t i=0;i<d.size();i++){if(d[i]<0||!std::isfinite(h[i]))throw std::runtime_error("Invalid thermal sample");sh+=h[i];hh+=h[i]*h[i];sd+=d[i];dd+=double(d[i])*d[i];hd+=h[i]*d[i];}
    }
    Update preview(const Moves&m)const {
        Update u;u.dd=dd;u.hd=hd;
        for(auto[a,b]:m){(void)d.at(a);(void)d.at(b);--u.delta[a];++u.delta[b];}
        for(auto[i,k]:u.delta){if(d[i]+k<0)throw std::runtime_error("V45 density underflow");u.dd+=2.*d[i]*k+double(k)*k;u.hd+=h[i]*k;}
        return u;
    }
    double value()const{return rho(dd,hd);}
    double after(const Update&u)const{return rho(u.dd,u.hd);}
    // A validated update is committed immediately, with no allocating work.
    void apply(const Update&u)noexcept{for(auto[i,k]:u.delta)d[i]+=k;dd=u.dd;hd=u.hd;}
    const std::vector<int>&counts()const{return d;}
};
enum class Mode {STRICT_PARETO=0,PHI_FIRST_WITH_BOUNDED_OVERFLOW=1,QUALITY_AWARE_PARETO=2};
struct Quality {double phi=0,wire=0,rho=0;long long overflow=0,vias=0;};
struct Limits {Mode mode=Mode::STRICT_PARETO;double wire_growth=.005,rho_loss=1e-5,overflow_growth=.005;long long max_overflow=64,max_vias=16;};
inline const char* reject(const Quality&base,const Quality&old,const Quality&next,const Limits&l){
    if(!std::isfinite(next.phi)||!std::isfinite(next.wire)||!std::isfinite(next.rho)||next.phi<0||next.wire<0||next.overflow<0||next.vias<0)return "invalid_quality";
    if(next.wire>base.wire*(1+l.wire_growth)+1e-6)return "global_wire_budget";
    if(next.rho+1e-12<base.rho-l.rho_loss)return "global_thermal_guard";
    if(next.vias>base.vias+l.max_vias)return "global_via_budget";
    if(next.phi>old.phi+1e-9)return "phi_regression";
    if(l.mode==Mode::STRICT_PARETO){
        if(next.overflow>old.overflow)return "strict_overflow";
        return next.phi<old.phi-1e-8||next.overflow<old.overflow?nullptr:"no_gain";
    }
    const long long budget=std::min(l.max_overflow,(long long)std::floor(base.overflow*l.overflow_growth));
    if(next.overflow>base.overflow+budget)return "cumulative_overflow_budget";
    if(next.phi>=old.phi-1e-8)return "no_phi_gain";
    // A positive cumulative O debit must buy a larger fractional Phi gain.
    double debit=double(std::max(0LL,next.overflow-base.overflow))/std::max(1LL,base.overflow);
    if(base.phi-next.phi+1e-9<2*debit*std::max(1.,base.phi))return "insufficient_phi_for_overflow";
    if(l.mode==Mode::QUALITY_AWARE_PARETO){
        double gain=(old.phi-next.phi)/std::max(1.,base.phi);
        double cost=std::max(0.,next.wire-old.wire)/std::max(1.,base.wire)+std::max(0.,old.rho-next.rho);
        if(gain<=cost+1e-10)return "joint_quality_budget";
    }
    return nullptr;
}
}
