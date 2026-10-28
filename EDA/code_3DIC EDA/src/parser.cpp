#include "model.hpp"
#include <fstream>
#include <regex>
#include <algorithm>
#include <iostream>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#endif
namespace gr {
std::string read_file(const std::filesystem::path&p){std::ifstream f(p,std::ios::binary);if(!f)throw std::runtime_error("Cannot read: "+p.u8string());return std::string(std::istreambuf_iterator<char>(f),{});}
void write_json(const std::filesystem::path&p,const std::function<void(std::ostream&)>&writer){
    if(p.has_parent_path())std::filesystem::create_directories(p.parent_path());
    auto temp=p;temp+=".tmp";
    if(std::filesystem::exists(temp))throw std::runtime_error("Temporary JSON already exists; preserve it and use a new output: "+temp.u8string());
    std::ofstream o(temp,std::ios::binary);if(!o)throw std::runtime_error("Cannot open temporary JSON: "+temp.u8string());
    o.exceptions(std::ios::badbit|std::ios::failbit);
    try{writer(o);o<<'\n';o.flush();o.close();}
    catch(const std::ios_base::failure&e){
        try{o.clear();o.flush();o.close();}catch(...){}
        throw std::runtime_error("JSON output failed; temporary file retained at "+temp.u8string()+": "+e.what());
    }catch(...){try{o.flush();o.close();}catch(...){}throw;}
    // Rename replaces an old complete file atomically on Linux. Do not unlink
    // the destination first: a failed publish must retain any previous result.
#ifdef _WIN32
    if(!MoveFileExW(temp.c_str(),p.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("JSON publish failed (Windows error "+std::to_string(GetLastError())+"); temporary file retained at "+temp.u8string());
#else
    std::error_code error;std::filesystem::rename(temp,p,error);
    if(error)throw std::runtime_error("JSON publish failed: "+error.message()+"; temporary file retained at "+temp.u8string());
#endif
}
void write_json(const std::filesystem::path&p,const Json&j){write_json(p,[&](std::ostream&o){j.write(o);});}
int layer_number(const std::string&s){std::smatch m;if(!std::regex_match(s,m,std::regex("(?:metal|m)([0-9]+)",std::regex::icase)))return 0;return std::stoi(m[1]);}
struct Tokens {
    std::vector<std::string> t;std::size_t p=0;
    Tokens(const std::string&s){for(size_t i=0;i<s.size();){unsigned char c=s[i];if(std::isspace(c)){++i;continue;}if(c=='#'){while(i<s.size()&&s[i]!='\n')++i;continue;}if(c=='"'){size_t b=++i;while(i<s.size()&&s[i]!='"'){if(s[i]=='\\'&&i+1<s.size())++i;++i;}if(i==s.size())throw std::runtime_error("Unclosed quoted string");t.push_back(s.substr(b,i-b));++i;continue;}if(c=='('||c==')'||c==';'||c=='+'){t.emplace_back(1,c);++i;continue;}size_t b=i;while(i<s.size()&&!std::isspace((unsigned char)s[i])&&s[i]!='('&&s[i]!=')'&&s[i]!=';'&&s[i]!='+')++i;t.push_back(s.substr(b,i-b));}}
    bool more()const{return p<t.size();}std::string next(){if(!more())throw std::runtime_error("Unexpected LEF/DEF end");return t[p++];}
    std::string peek()const{return more()?t[p]:"";}void expect(const std::string&s){auto v=next();if(v!=s)throw std::runtime_error("Expected "+s+", got "+v);}
    double number(){auto v=next();size_t n;double x=std::stod(v,&n);if(n!=v.size()||!std::isfinite(x))throw std::runtime_error("Invalid numeric token: "+v);return x;}
    void semi(){while(more()&&next()!=";"){} }
    std::vector<std::string> record(){std::vector<std::string>a;while(more()){auto v=next();if(v==";")return a;a.push_back(v);}throw std::runtime_error("Missing semicolon");}
};
std::pair<double,double> orient_point(double x,double y,double w,double h,const std::string&o){
    if(o=="N")return {x,y};
    if(o=="S")return {w-x,h-y};
    if(o=="W")return {h-y,x};
    if(o=="E")return {y,w-x};
    if(o=="FN")return {w-x,y};
    if(o=="FS")return {x,h-y};
    if(o=="FW")return {h-y,w-x};
    if(o=="FE")return {y,x};
    throw std::runtime_error("Unsupported orientation: "+o);
}
Rect placed_rect(const Rect&r,const Macro&m,const Instance&i,double dbu){Rect q{1e100,1e100,-1e100,-1e100,r.layer};for(double x:{r.x1,r.x2})for(double y:{r.y1,r.y2}){auto[a,b]=orient_point(x+m.ox,y+m.oy,m.width,m.height,i.orient);q.x1=std::min(q.x1,i.x+a*dbu);q.x2=std::max(q.x2,i.x+a*dbu);q.y1=std::min(q.y1,i.y+b*dbu);q.y2=std::max(q.y2,i.y+b*dbu);}return q;}
void parse_lef(const std::filesystem::path&p,Design&d){
    Tokens s(read_file(p));
    while(s.more()){
        auto k=s.next();
        if(k=="UNITS"||k=="PROPERTYDEFINITIONS"){
            while(s.more()){if(s.next()=="END"){s.expect(k);break;}}
        }else if(k=="LAYER"){
            std::string name=s.next();Layer l;l.name=name;
            while(s.more()){auto a=s.next();if(a=="END"){s.expect(name);break;}if(a=="TYPE"){l.type=s.next();s.semi();}else if(a=="DIRECTION"){l.direction=s.next();s.semi();}else if(a=="WIDTH"){l.width=s.number();s.semi();}else if(a=="SPACING"){l.spacing=s.number();s.semi();}else if(a=="PITCH"){l.pitch=s.number();s.semi();}}
            int n=layer_number(name);if(l.type=="ROUTING"){if(n<1||n>20)throw std::runtime_error("Contest supports metal1..metal20 only: "+name);if(l.direction!="HORIZONTAL"&&l.direction!="VERTICAL")throw std::runtime_error("Missing layer direction: "+name);if(d.layers.count(n))throw std::runtime_error("Duplicate routing layer");d.layers[n]=l;}
            if(l.type=="CUT"&&(name=="tsv_layer"||name=="TSV"))d.tsv_cut=true;
        }else if(k=="VIA"||k=="VIARULE"){
            auto name=s.next();std::set<int> ls;while(s.more()){auto a=s.next();if(a=="END"){s.expect(name);break;}if(a=="LAYER"){int n=layer_number(s.next());if(n)ls.insert(n);}}
            if(ls.size()==2){int a=*ls.begin(),b=*ls.rbegin();if(b==a+1)d.vias.insert({a,b});}
        }else if(k=="MACRO"){
            auto name=s.next();Macro m;
            while(s.more()){
                auto a=s.next();if(a=="END"){s.expect(name);break;}
                if(a=="SIZE"){m.width=s.number();s.expect("BY");m.height=s.number();s.semi();}
                else if(a=="ORIGIN"){m.ox=s.number();m.oy=s.number();s.semi();}
                else if(a=="PIN"||a=="OBS"){
                    bool pin=a=="PIN";std::string pn=pin?s.next():"";std::vector<Rect> rs;int layer=0;
                    while(s.more()){
                        auto b=s.next();if(b=="END") {if(!pin)break;if(s.peek()==pn){s.next();break;}continue;}
                        if(b=="LAYER"){layer=layer_number(s.next());s.semi();}
                        else if(b=="RECT"){if(s.peek()=="MASK")throw std::runtime_error("LEF MASK geometry unsupported");Rect q;q.layer=layer;q.x1=s.number();q.y1=s.number();q.x2=s.number();q.y2=s.number();s.expect(";");if(layer)rs.push_back(q);}
                        else if(b=="POLYGON"||b=="PATH"||b=="ITERATE")throw std::runtime_error("Unsupported LEF geometry "+b+" in "+name+"."+pn);
                    }
                    if(pin)m.pins[pn]=rs;else m.obs=rs;
                }
            }
            if(m.width<=0||m.height<=0)throw std::runtime_error("Invalid macro SIZE: "+name);
            d.macros[name]=std::move(m);
        }
    }
    if(d.layers.empty()||d.macros.empty())throw std::runtime_error("LEF lacks routing layers or macros");
}
static double num(const std::vector<std::string>&a,size_t i){if(i>=a.size())throw std::runtime_error("Truncated DEF record");size_t n;double v=std::stod(a[i],&n);if(n!=a[i].size()||!std::isfinite(v))throw std::runtime_error("Invalid DEF number");return v;}
static size_t find(const std::vector<std::string>&a,const std::string&k){auto p=std::find(a.begin(),a.end(),k);return size_t(p-a.begin());}
static Instance placement(const std::vector<std::string>&a){size_t j=find(a,"PLACED");if(j==a.size())j=find(a,"FIXED");if(j==a.size())j=find(a,"COVER");if(j+5>=a.size()||a[j+1]!="("||a[j+4]!=")")throw std::runtime_error("Missing valid DEF placement");Instance i;i.x=num(a,j+2);i.y=num(a,j+3);i.orient=a[j+5];orient_point(0,0,0,0,i.orient);return i;}
void parse_def(const std::filesystem::path&p,Design&d){
    Tokens s(read_file(p));std::map<std::string,std::vector<Pin>> io;std::vector<std::pair<std::string,std::vector<std::pair<std::string,std::string>>>> raw;
    while(s.more()){
        auto k=s.next();
        if(k=="END"){s.expect("DESIGN");if(s.more())throw std::runtime_error("Unexpected data after END DESIGN");break;}
        else if(k=="VERSION"){d.version=s.next();s.expect(";");}
        else if(k=="DESIGN"){d.name=s.next();s.expect(";");}
        else if(k=="UNITS"){s.expect("DISTANCE");s.expect("MICRONS");d.dbu=s.number();s.expect(";");}
        else if(k=="DIEAREA"){s.expect("(");d.die.x1=s.number();d.die.y1=s.number();s.expect(")");s.expect("(");d.die.x2=s.number();d.die.y2=s.number();s.expect(")");s.expect(";");}
        else if(k=="TRACKS"||k=="GCELLGRID"){
            char axis=s.next().at(0);if(axis!='X'&&axis!='Y')throw std::runtime_error("Invalid grid axis");double start=s.number();s.expect("DO");int count=int(s.number());s.expect("STEP");double step=s.number();if(count<1||step<=0||count>10000000)throw std::runtime_error("Invalid grid/track pattern");
            if(k=="TRACKS"){s.expect("LAYER");while(s.peek()!=";"){int l=layer_number(s.next());if(!d.layers.count(l))throw std::runtime_error("TRACKS refers to unknown layer");d.tracks.push_back({axis,start,step,count,l});}s.expect(";");}
            else{for(int j=0;j<count;j++)d.grid[axis].push_back(start+j*step);s.expect(";");}
        }else if(k=="VIAS"||k=="BLOCKAGES"||k=="SPECIALNETS"||k=="REGIONS"||k=="GROUPS"){
            int count=int(s.number());s.expect(";");
            if(count!=0)throw std::runtime_error("Unsupported nonempty DEF section: "+k);
            s.expect("END");s.expect(k);
        }else if(k=="COMPONENTS"||k=="PINS"||k=="NETS"){
            int count=int(s.number());s.expect(";");int seen=0;
            while(s.peek()!="END"){
                s.expect("-");auto a=s.record();++seen;if(a.empty())throw std::runtime_error("Empty DEF record");
                if(k=="COMPONENTS"){if(a.size()<2)throw std::runtime_error("Missing macro");auto i=placement(a);i.name=a[0];i.macro=a[1];if(!d.macros.count(i.macro))throw std::runtime_error("Unknown macro: "+i.macro);if(!d.instances.emplace(i.name,i).second)throw std::runtime_error("Duplicate instance");}
                else if(k=="PINS"){
                    auto place=placement(a);std::vector<Pin> pins;
                    for(size_t j=0;j+9<a.size();j++)if(a[j]=="LAYER"){
                        int l=layer_number(a[j+1]);if(!d.layers.count(l))throw std::runtime_error("Unknown IO pin layer");if(a[j+2]!="("||a[j+5]!=")"||a[j+6]!="("||a[j+9]!=")")throw std::runtime_error("Unsupported IO geometry");
                        double x=(num(a,j+3)+num(a,j+7))/2,y=(num(a,j+4)+num(a,j+8))/2;
                        // IO placement is an origin; its local RECT is rotated without bbox compensation.
                        auto [u,v]=orient_point(x,y,0,0,place.orient);pins.push_back({"PIN."+a[0],place.x+u,place.y+v,l});
                    }
                    if(pins.empty())throw std::runtime_error("IO pin has no rectangular port: "+a[0]);
                    io[a[0]]=pins;
                }else{
                    std::vector<std::pair<std::string,std::string>> cs;
                    for(size_t j=1;j<a.size()&&a[j]!="+";){if(j+3>=a.size()||a[j]!="("||a[j+3]!=")")throw std::runtime_error("Unsupported NET connection: "+a[0]);cs.emplace_back(a[j+1],a[j+2]);j+=4;}
                    if(cs.empty())throw std::runtime_error("Net without terminals: "+a[0]);
                    raw.push_back({a[0],cs});
                }
            }s.expect("END");s.expect(k);if(seen!=count)throw std::runtime_error("DEF section count mismatch: "+k);
        }
    }
    if(d.version.empty()||d.dbu<=0||d.die.x2<=d.die.x1||d.die.y2<=d.die.y1||raw.empty())throw std::runtime_error("DEF missing VERSION/UNITS/DIEAREA/NETS");
    if(d.grid['X'].empty()||d.grid['Y'].empty()||d.tracks.empty())throw std::runtime_error("DEF requires TRACKS and GCELLGRID");
    std::set<std::string> names;
    for(auto&[name,cs]:raw){Net n;n.name=name;if(!names.insert(name).second)throw std::runtime_error("Duplicate net: "+name);
        for(auto&[in,pn]:cs){Pin pin;
            if(in=="PIN"){if(!io.count(pn))throw std::runtime_error("Undefined IO pin: "+pn);pin=io.at(pn).front();}
            else{if(!d.instances.count(in))throw std::runtime_error("Undefined instance: "+in);auto&i=d.instances.at(in);auto&m=d.macros.at(i.macro);if(!m.pins.count(pn)||m.pins.at(pn).empty())throw std::runtime_error("Undefined macro pin: "+in+"."+pn);auto q=placed_rect(m.pins.at(pn).front(),m,i,d.dbu);pin={in+"."+pn,(q.x1+q.x2)/2,(q.y1+q.y2)/2,q.layer};}
            if(!d.layers.count(pin.layer))throw std::runtime_error("Unknown pin layer");
            if(pin.x<d.die.x1||pin.x>d.die.x2||pin.y<d.die.y1||pin.y>d.die.y2)throw std::runtime_error("Pin outside DIEAREA: "+pin.name);
            n.pins.push_back(pin);
        }
        bool bottom=false,top=false;for(auto&pin:n.pins){bottom|=pin.layer<=10;top|=pin.layer>=11;}n.cross=bottom&&top;d.nets.push_back(std::move(n));
    }
}
Trace::Trace(const std::string&p,std::size_t mb):limit(mb*1024*1024){if(!p.empty()){std::filesystem::path q=std::filesystem::u8path(p);if(q.has_parent_path())std::filesystem::create_directories(q.parent_path());out=new std::ofstream(q);if(!*out)throw std::runtime_error("Cannot open trace");}}
Trace::~Trace(){delete out;}
void Trace::event(std::string type,Json d){if(!out||truncated)return;d["event"]=type;d["seq"]=seq++;auto line=d.dump();if(bytes+line.size()+1>limit){Json e=Json::object();e["event"]="trace_truncated";e["seq"]=seq++;e["reason"]="size_limit";*out<<e.dump()<<'\n';truncated=true;return;}*out<<line<<'\n';bytes+=line.size()+1;if(!*out)throw std::runtime_error("Trace write failed");}
}
