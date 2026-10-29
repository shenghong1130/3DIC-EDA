#pragma once
#include <algorithm>
#include <array>
#include <vector>
#include <limits>
#include <numeric>
#include <cmath>
#include <stdexcept>
namespace gr {
// Exact nearest-point distance in weighted L1 space. Bounding boxes belong to
// small KD subtrees, not to one loose box around the complete routed tree.
class L1TargetIndex {
    struct Node {std::array<double,3> p,lo,hi;int left=-1,right=-1;};
    std::vector<Node> nodes_;int root_=-1;
    int build(int first,int last,int axis){
        if(first==last)return -1;
        int mid=first+(last-first)/2;
        std::nth_element(nodes_.begin()+first,nodes_.begin()+mid,nodes_.begin()+last,[axis](const Node&a,const Node&b){return a.p[axis]!=b.p[axis]?a.p[axis]<b.p[axis]:a.p<b.p;});
        int left=build(first,mid,(axis+1)%3),right=build(mid+1,last,(axis+1)%3);
        auto&n=nodes_[mid];n.left=left;n.right=right;n.lo=n.hi=n.p;
        for(int child:{left,right})if(child>=0)for(int k=0;k<3;k++){n.lo[k]=std::min(n.lo[k],nodes_[child].lo[k]);n.hi[k]=std::max(n.hi[k],nodes_[child].hi[k]);}
        return mid;
    }
    double bound(int node,const std::array<double,3>&p)const{
        if(node<0)return std::numeric_limits<double>::infinity();
        double value=0;for(int k=0;k<3;k++)value+=std::max({nodes_[node].lo[k]-p[k],0.0,p[k]-nodes_[node].hi[k]});return value;
    }
    void query(int node,const std::array<double,3>&p,double&best)const{
        if(node<0||bound(node,p)>best)return;
        const auto&n=nodes_[node];double value=0;for(int k=0;k<3;k++)value+=std::abs(p[k]-n.p[k]);best=std::min(best,value);
        int a=n.left,b=n.right;if(bound(a,p)>bound(b,p))std::swap(a,b);query(a,p,best);query(b,p,best);
    }
public:
    explicit L1TargetIndex(const std::vector<std::array<double,3>>&points){
        nodes_.reserve(points.size());for(auto p:points)nodes_.push_back({p,p,p,-1,-1});root_=build(0,int(nodes_.size()),0);
    }
    double distance(const std::array<double,3>&point)const{double best=std::numeric_limits<double>::infinity();query(root_,point,best);return best;}
};
// Sufficient statistics for the joint TSV-density/temperature correlation.
// A move updates two bins; this is not independent per-Net hot-spot scoring.
class DensityCorrelation {
    const std::vector<double>&heat_;std::vector<int> counts_;
    double sh_=0,shh_=0,sd_=0,sdd_=0,shd_=0;
    double correlation(double dd,double hd)const{
        double n=double(counts_.size()),a=shh_-sh_*sh_/n,b=dd-sd_*sd_/n;
        return a>1e-20&&b>1e-20?std::clamp((hd-sh_*sd_/n)/std::sqrt(a*b),-1.0,1.0):0;
    }
public:
    DensityCorrelation(const std::vector<double>&h,std::vector<int>counts):heat_(h),counts_(std::move(counts)){
        if(h.empty()||h.size()!=counts_.size())throw std::runtime_error("Invalid TSV density grid");
        for(size_t i=0;i<h.size();i++){if(counts_[i]<0)throw std::runtime_error("Negative TSV density");sh_+=h[i];shh_+=h[i]*h[i];sd_+=counts_[i];sdd_+=double(counts_[i])*counts_[i];shd_+=h[i]*counts_[i];}
    }
    double value()const{return correlation(sdd_,shd_);}
    double after_move(size_t a,size_t b)const{
        counts_.at(a);counts_.at(b);
        if(a==b)return value();
        if(counts_.at(a)<=0)throw std::runtime_error("TSV density underflow");
        return correlation(sdd_+2.0*(counts_.at(b)-counts_[a])+2,shd_+heat_[b]-heat_[a]);
    }
    void move(size_t a,size_t b){
        counts_.at(a);counts_.at(b);
        if(a==b)return;
        if(counts_.at(a)<=0)throw std::runtime_error("TSV density underflow");
        sdd_+=2.0*(counts_.at(b)-counts_[a])+2;shd_+=heat_[b]-heat_[a];--counts_[a];++counts_[b];
    }
    const std::vector<int>&counts()const{return counts_;}
};
}
