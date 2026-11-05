#include "sparse_tsv_assignment.hpp"
#include <iostream>
#include <random>
using namespace gr::tsv_assignment;
void require(bool b,const char*message){if(!b)throw std::runtime_error(message);}
double oracle(const Problem&p,const Solution&s){
    std::map<Key,int> use,sites;double value=0;
    for(size_t i=0;i<p.nets.size();i++){const auto&c=p.nets[i].at(s.choice[i]);value+=c.access_cost;++sites[c.site];for(auto[v,n]:c.demand)use[v]+=n;}
    for(auto[v,r]:p.resources){double over=std::max(0,r.fixed+use[v]-r.capacity);value+=p.phi_weight*over*over/r.capacity/r.capacity;}
    for(auto[v,n]:sites)value+=p.sharing_weight*n*(n-1)/2.;return value;
}
int main(){try{
    Problem p;p.resources={{10,{1,0}},{11,{1,0}},{20,{1,0}},{21,{1,0}}};
    Choice a{100,0,{{10,1},{11,1}}},b{200,0,{{20,1},{21,1}}};
    p.nets={{a,b},{a,b}};auto s=solve(p);require(s.choice[0]!=s.choice[1]&&s.phi==0&&s.shared_sites==0,"Co-located TSVs not dispersed");
    p.resources[11].fixed=1;p.nets={{a,b}};s=solve(p);require(s.choice[0]==1,"Top die conflict ignored");
    p.resources[11].fixed=0;p.resources[10].fixed=1;s=solve(p);require(s.choice[0]==1,"Bottom die conflict ignored");
    p.resources[10].fixed=0;p.nets={{a},{a}};s=solve(p);require(s.shared_sites==1&&s.phi==2,"Capacity-constrained sharing forbidden");
    p.resources[10].capacity=p.resources[11].capacity=10;s=solve(p);require(s.shared_sites==1&&s.phi==0,"Legal high-capacity sharing overcharged");
    // Distinct landing centres can still overlap a multi-GCell footprint.
    p.resources[10].capacity=p.resources[11].capacity=1;
    Choice overlap{300,0,{{10,1},{21,1}}};p.nets={{a},{overlap,b}};s=solve(p);require(s.choice[1]==1,"Neighbouring footprint overlap ignored");
    // Neither individual move pays off, but a paired exchange does.
    a.access_cost=0;b.access_cost=2;p.nets={{a,b}};a.access_cost=1;b.access_cost=4;p.nets.push_back({a,b});
    s=solve(p);require(s.swaps==1&&s.choice[0]==1&&s.choice[1]==0&&s.objective==3,"Joint exchange stuck at greedy allocation");
    bool interrupted=false;int polls=0;try{solve(p,[&](){if(++polls==5)throw std::runtime_error("timeout");});}catch(const std::runtime_error&){interrupted=true;}
    require(interrupted&&solve(p).objective==3,"Interrupted planning mutated problem");
    p.resources[10].capacity=0;bool rejected=false;try{solve(p);}catch(const std::invalid_argument&){rejected=true;}require(rejected,"Zero capacity accepted");p.resources[10].capacity=1;
    auto saved=p.nets;p.nets[0][0].demand.push_back({10,1});rejected=false;try{solve(p);}catch(const std::invalid_argument&){rejected=true;}require(rejected,"Repeated resource charged twice");p.nets=saved;
    std::mt19937 rng(42);
    for(int test=0;test<100;test++){
        Problem q;for(int v=0;v<12;v++)q.resources[v]={1+int(rng()%5),int(rng()%3)};
        for(int i=0;i<20;i++){std::vector<Choice> net;for(int k=0;k<6;k++)net.push_back({k,double(rng()%20),{{2*k,1+int(rng()%2)},{2*k+1,1+int(rng()%2)}}});q.nets.push_back(net);}
        auto result=solve(q);require(std::abs(oracle(q,result)-result.objective)<1e-8,"Incremental objective differs from full oracle");require(result.choice==solve(q).choice,"Non-deterministic planning");
    }
    std::cout<<"PASS: co-location, two-die capacity, legal sharing, footprint overlap, pair swap, timeout isolation, zero-capacity and duplicate rejection, 100 full-objective/determinism oracles\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
