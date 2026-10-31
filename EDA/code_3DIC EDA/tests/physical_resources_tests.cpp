#include "physical_resources.hpp"
#include <iostream>
#include <random>
using namespace gr;
void require(bool b,const char*s){if(!b)throw std::runtime_error(s);}
int main(){try{
    Design d;d.dbu=1000;d.die={0,0,8000,80000,0};d.grid['X']={0,1000,2000,3000,4000,5000,6000,7000,8000};for(int i=0;i<=80;i++)d.grid['Y'].push_back(i*1000);
    for(int l:{1,2}){d.layers[l]={"metal"+std::to_string(l),"ROUTING",l==1?"HORIZONTAL":"VERTICAL",.1,0,.2};d.tracks.push_back({l==1?'Y':'X',50,200,l==1?400:40,l});}
    Macro m;m.width=8;m.height=80;std::mt19937 rng(7);for(int i=0;i<700;i++){int x=rng()%7600,y=rng()%79000,w=1+rng()%400,h=1+rng()%700;Rect r{double(x)/1000,double(y)/1000,double(x+w)/1000,double(y+h)/1000,1+i%2};if(i%3)m.pins[std::to_string(i)].push_back(r);else m.obs.push_back(r);}d.macros["m"]=m;d.instances["i"]={"i","m","N",0,0};
    PhysicalModel model(d);std::map<Id,std::set<double>> expected;size_t intervals=0;
    // Independent point-set oracle: scans Tracks rather than production bit ranges.
    physical_rectangles(d,[&](Rect r,bool pin){model.grid.cells(r,[&](Id v,int x,int y,Rect q){++intervals;int l=v/model.grid.plane;bool h=d.layers.at(l).direction=="HORIZONTAL";std::vector<double> inside;for(auto&t:d.tracks)if(t.layer==l)for(int k=0;k<t.count;k++){double z=t.start+k*t.step,lo=h?model.grid.ys[y]:model.grid.xs[x],hi=h?model.grid.ys[y+1]:model.grid.xs[x+1];if(z>=lo&&z<hi)inside.push_back(z);}bool found=false;for(double z:inside)if(z>=(h?q.y1:q.x1)&&z<=(h?q.y2:q.x2)){expected[v].insert(z);found=true;}if(pin&&!found){if(inside.empty())expected[v].insert(-1);else{double middle=h?(q.y1+q.y2)/2:(q.x1+q.x2)/2;double best=inside[0];for(double z:inside)if(std::abs(z-middle)<std::abs(best-middle))best=z;expected[v].insert(best);}}});});
    std::map<Id,int> actual;model.build([&](Id v,int,int f){actual[v]=f;},[](){},8*8);for(auto&[v,s]:expected)require(actual[v]==int(s.size()),"Track-union oracle mismatch");require(model.intervals==intervals,"Interval accounting mismatch");require(model.max_workspace_bytes<=64,"Stripe memory exceeded");
    PhysicalModel second(d);std::map<Id,int> large;second.build([&](Id v,int,int f){large[v]=f;});require(actual==large,"Stripe-size-dependent result");
    bool interrupted=false;try{PhysicalModel fail(d);fail.build([](Id,int,int){},[](){throw std::runtime_error("injected interruption");});}catch(const std::runtime_error&){interrupted=true;}require(interrupted,"Interruption not propagated");
    std::cout<<"Physical bitmap: random union oracle, no-Track Pins, stripe boundaries, workspace bound and interruption PASS\n";return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
