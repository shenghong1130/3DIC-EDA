#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include <limits>
#include <map>
#include <numeric>
#include <stdexcept>
#include <vector>

// Independently implemented experimental planner. It consumes the Router's
// physical footprint; it does not define a second resource/scoring model.
namespace gr::tsv_assignment {
using Key = std::int64_t;
struct Resource { int capacity=0, fixed=0; };
struct Choice {
    Key site=0;
    double access_cost=0;
    std::vector<std::pair<Key,int>> demand; // sorted, unique, full two-die footprint
};
struct Problem {
    std::map<Key,Resource> resources;
    std::vector<std::vector<Choice>> nets;
    double phi_weight=32, sharing_weight=2;
};
struct Solution {
    std::vector<size_t> choice;
    double objective=0, phi=0;
    size_t shared_sites=0, moves=0, swaps=0;
};
inline Solution solve(const Problem& p, const std::function<void()>& poll=[](){}) {
    if(!std::isfinite(p.phi_weight)||p.phi_weight<0||!std::isfinite(p.sharing_weight)||p.sharing_weight<0)
        throw std::invalid_argument("Invalid TSV weights");
    std::map<Key,long long> use, sites;
    for(const auto& [v,r]:p.resources) {
        if(r.capacity<=0||r.fixed<0)throw std::invalid_argument("Nonpositive TSV capacity");
        use.emplace(v,0);
    }
    size_t entries=0;
    for(const auto& net:p.nets) {
        poll();if(net.empty()||net.size()>24)throw std::invalid_argument("TSV candidate count");
        for(const auto& c:net) {
            if(!std::isfinite(c.access_cost)||c.access_cost<0||c.demand.empty())throw std::invalid_argument("Invalid TSV candidate");
            sites.emplace(c.site,0);Key prior=0;bool first=true;
            for(auto [v,n]:c.demand) {
                if(n<=0||!p.resources.count(v)||(!first&&v<=prior))throw std::invalid_argument("Invalid TSV footprint");
                first=false;prior=v;if(++entries>4000000)throw std::length_error("TSV sparse edge budget");
            }
        }
    }
    auto phi=[&](Key v,long long n){const auto&r=p.resources.at(v);double over=std::max(0.,double(r.fixed)+n-r.capacity);return std::pow(over/r.capacity,2);};
    auto add=[&](const Choice&c,int sign){for(auto[v,n]:c.demand)use.at(v)+=sign*n;sites.at(c.site)+=sign;};
    auto cost=[&](const Choice&c){double x=c.access_cost+p.sharing_weight*sites.at(c.site);
        for(auto[v,n]:c.demand)x+=p.phi_weight*(phi(v,use.at(v)+n)-phi(v,use.at(v)));return x;};
    Solution out;out.choice.resize(p.nets.size());
    std::vector<size_t> order(p.nets.size());std::iota(order.begin(),order.end(),0);
    // Constrained Nets first. Subsequent global sweeps remove order-dependent
    // allocations; pair exchanges can cross a single-move local minimum.
    std::stable_sort(order.begin(),order.end(),[&](size_t a,size_t b){return p.nets[a].size()<p.nets[b].size();});
    for(size_t i:order){poll();size_t best=0;double c=cost(p.nets[i][0]);for(size_t k=1;k<p.nets[i].size();k++){double v=cost(p.nets[i][k]);if(v<c-1e-10){best=k;c=v;}}out.choice[i]=best;add(p.nets[i][best],1);}
    for(int round=0;round<4;round++) {
        size_t changes=0;
        for(size_t j=0;j<order.size();j++) {
            poll();size_t i=order[round%2?order.size()-1-j:j],old=out.choice[i];add(p.nets[i][old],-1);
            size_t best=old;double c=cost(p.nets[i][old]);
            for(size_t k=0;k<p.nets[i].size();k++){double v=cost(p.nets[i][k]);if(v<c-1e-10){best=k;c=v;}}
            add(p.nets[i][best],1);out.choice[i]=best;changes+=best!=old;out.moves+=best!=old;
        }
        if(!changes)break;
    }
    std::map<Key,std::vector<size_t>> occupants;
    for(size_t i=0;i<p.nets.size();i++)occupants[p.nets[i][out.choice[i]].site].push_back(i);
    size_t examined=0;
    for(size_t i=0;i<p.nets.size()&&examined<12000;i++)for(size_t k=0;k<p.nets[i].size()&&examined<12000;k++) {
        auto it=occupants.find(p.nets[i][k].site);if(it==occupants.end())continue;
        // This frozen index is only a bounded proposal generator. Exact costs
        // always use current assignments, so stale proposals cannot corrupt use.
        for(size_t j:it->second) {
            if(examined++>=12000)break;poll();if(i==j)continue;
            size_t a=out.choice[i],b=out.choice[j];Key destination=p.nets[i][a].site;
            auto jt=std::find_if(p.nets[j].begin(),p.nets[j].end(),[&](const Choice&c){return c.site==destination;});
            if(jt==p.nets[j].end())continue;
            size_t q=size_t(jt-p.nets[j].begin());add(p.nets[i][a],-1);add(p.nets[j][b],-1);
            auto pair_cost=[&](const Choice&x,const Choice&y){double c=cost(x);add(x,1);c+=cost(y);add(x,-1);return c;};
            double old=pair_cost(p.nets[i][a],p.nets[j][b]),next=pair_cost(p.nets[i][k],*jt);
            if(next<old-1e-10){a=k;b=q;++out.swaps;}add(p.nets[i][a],1);add(p.nets[j][b],1);out.choice[i]=a;out.choice[j]=b;
        }
    }
    for(auto[v,n]:use){if(n<0)throw std::logic_error("TSV negative planned demand");out.phi+=phi(v,n);}
    out.objective=p.phi_weight*out.phi;
    for(size_t i=0;i<p.nets.size();i++)out.objective+=p.nets[i][out.choice[i]].access_cost;
    for(auto[v,n]:sites){out.shared_sites+=n>1;out.objective+=p.sharing_weight*double(n)*(n-1)/2;}
    return out;
}
}
