#include "v45_quality_guard.hpp"
#include "sparse_tsv_assignment.hpp"
#include <random>
#include <iostream>
using namespace gr;
void need(bool b,const char*m){if(!b)throw std::runtime_error(m);}
int main(){try{
    std::mt19937 gen(45);
    for(int t=0;t<300;t++){
        std::vector<double>h(71);std::vector<int>counts(71);
        for(size_t i=0;i<h.size();i++){h[i]=double(gen()%100);counts[i]=gen()%6;}
        v45::Thermal c(h,counts);v45::Thermal::Moves moves;
        for(int j=0;j<8;j++){size_t a=gen()%71,b=gen()%71;if(counts[a]){moves.emplace_back(a,b);--counts[a];++counts[b];}}
        auto u=c.preview(moves);v45::Thermal oracle(h,counts);need(std::abs(c.after(u)-oracle.value())<1e-12,"Batch Pearson disagrees with full rebuild");
        c.apply(u);need(c.counts()==counts&&std::abs(c.value()-oracle.value())<1e-12,"Thermal commit mismatch");
    }
    std::vector<double>h{0,1,3};v45::Thermal c(h,{2,0,0});auto u=c.preview({{0,1},{0,1}});v45::Thermal oracle(h,{0,2,0});need(std::abs(c.after(u)-oracle.value())<1e-12,"Shared destination cross term lost");
    bool bad=false;try{c.preview({{1,2}});}catch(const std::runtime_error&){bad=true;}need(bad,"Density underflow allowed");
    v45::Quality base{100,1000,.1,10000,100},next{90,1001,.1,10030,102};v45::Limits l;
    need(v45::reject(base,base,next,l)!=nullptr,"Strict Pareto accepted increased O");l.mode=v45::Mode::PHI_FIRST_WITH_BOUNDED_OVERFLOW;
    need(!v45::reject(base,base,next,l),"Bounded Phi-first rejected worthwhile gain");next.overflow=10065;need(v45::reject(base,base,next,l)!=nullptr,"Cumulative O cap exceeded");next.overflow=10030;next.phi=99.9;need(v45::reject(base,base,next,l)!=nullptr,"Tiny Phi gain bought excessive O");
    next={90,1006,.1,10000,100};need(v45::reject(base,base,next,l)!=nullptr,"Wire limit exceeded");next.wire=1000;next.rho=.099;need(v45::reject(base,base,next,l)!=nullptr,"Thermal guard missed");next.rho=.1;next.vias=200;need(v45::reject(base,base,next,l)!=nullptr,"Via limit missed");
    tsv_assignment::Problem p;p.resources={{1,{1,0}},{2,{1,0}},{3,{1,0}}};
    p.nets={{{10,0,{{1,1},{2,1}}},{11,.1,{{1,1}}}},{{20,0,{{2,1},{3,1}}},{21,.1,{{3,1}}}}};
    auto s=tsv_assignment::solve(p);need(s.phi==0,"Distinct centers overlapping footprints ignored");
    p.resources[1].capacity=0;bad=false;try{tsv_assignment::solve(p);}catch(const std::invalid_argument&){bad=true;}need(bad,"Zero capacity admitted");
    std::cout<<"PASS: 300 batch Pearson oracles, coincident updates, density underflow, strict/phi gates, cumulative O, wire/thermal/via caps, footprint overlap, C=0\n";
    return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
