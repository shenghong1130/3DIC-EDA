#pragma once
#include <map>
#include <vector>
#include <string>
#include <sstream>
#include <iomanip>
#include <stdexcept>
#include <cmath>
#include <cctype>
namespace gr {
// Small strict JSON value/parser. No external runtime or vendored dependencies.
struct Json {
    enum Type {Null, Number, String, Array, Object, Boolean} type=Null;
    double num=0; std::string str; std::vector<Json> arr; std::map<std::string,Json> obj;
    Json()=default; Json(double v):type(Number),num(v){} Json(int v):Json(double(v)){}
    Json(bool v):type(Boolean),num(v){} Json(const char* v):type(String),str(v){}
    Json(std::string v):type(String),str(std::move(v)){}
    static Json array(){Json j;j.type=Array;return j;}
    static Json object(){Json j;j.type=Object;return j;}
    Json& operator[](const std::string& k){if(type==Null)type=Object;if(type!=Object)throw std::runtime_error("JSON object expected");return obj[k];}
    const Json& at(const std::string& k)const{if(type!=Object||!obj.count(k))throw std::runtime_error("Missing JSON key: "+k);return obj.at(k);}
    double number()const{if(type!=Number||!std::isfinite(num))throw std::runtime_error("Finite JSON number expected");return num;}
    std::string string()const{if(type!=String)throw std::runtime_error("JSON string expected");return str;}
    void push(Json v){if(type!=Array)throw std::runtime_error("JSON array expected");arr.push_back(std::move(v));}
    static std::string quote(const std::string& s){std::ostringstream o;o<<'"';for(unsigned char c:s){switch(c){case '"':o<<"\\\"";break;case '\\':o<<"\\\\";break;case '\n':o<<"\\n";break;case '\r':o<<"\\r";break;case '\t':o<<"\\t";break;default:if(c<32)o<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<int(c)<<std::dec;else o<<c;}}o<<'"';return o.str();}
    void write(std::ostream& o)const{switch(type){case Null:o<<"null";break;case Number:if(!std::isfinite(num))throw std::runtime_error("Nonfinite JSON output");o<<std::setprecision(15)<<num;break;case Boolean:o<<(num?"true":"false");break;case String:o<<quote(str);break;case Array:{o<<'[';bool first=true;for(auto&v:arr){if(!first)o<<',';first=false;v.write(o);}o<<']';break;}case Object:{o<<'{';bool first=true;for(auto&[k,v]:obj){if(!first)o<<',';first=false;o<<quote(k)<<':';v.write(o);}o<<'}';break;}}}
    std::string dump()const{std::ostringstream o;write(o);return o.str();}
    struct Parser {
        const std::string& s;size_t i=0;
        void ws(){while(i<s.size()&&std::isspace((unsigned char)s[i]))++i;}
        char take(){if(i>=s.size())throw std::runtime_error("Unexpected JSON end");return s[i++];}
        void utf8(std::string&v,unsigned x){if(x<128)v+=char(x);else if(x<2048){v+=char(192|(x>>6));v+=char(128|(x&63));}else if(x<65536){v+=char(224|(x>>12));v+=char(128|((x>>6)&63));v+=char(128|(x&63));}else{v+=char(240|(x>>18));v+=char(128|((x>>12)&63));v+=char(128|((x>>6)&63));v+=char(128|(x&63));}}
        unsigned hex(){unsigned x=0;for(int k=0;k<4;k++){char c=take();x*=16;if(c>='0'&&c<='9')x+=c-'0';else if(c>='a'&&c<='f')x+=c-'a'+10;else if(c>='A'&&c<='F')x+=c-'A'+10;else throw std::runtime_error("Bad JSON unicode");}return x;}
        std::string text(){if(take()!='"')throw std::runtime_error("JSON quote expected");std::string v;for(;;){char c=take();if(c=='"')return v;if((unsigned char)c<32)throw std::runtime_error("JSON control character");if(c!='\\'){v+=c;continue;}c=take();switch(c){case '"':case '\\':case '/':v+=c;break;case 'b':v+='\b';break;case 'f':v+='\f';break;case 'n':v+='\n';break;case 'r':v+='\r';break;case 't':v+='\t';break;case 'u':{unsigned x=hex();if(x>=0xd800&&x<=0xdbff){if(take()!='\\'||take()!='u')throw std::runtime_error("Missing low surrogate");unsigned y=hex();if(y<0xdc00||y>0xdfff)throw std::runtime_error("Bad surrogate");x=0x10000+((x-0xd800)<<10)+(y-0xdc00);}else if(x>=0xdc00&&x<=0xdfff)throw std::runtime_error("Unpaired surrogate");utf8(v,x);break;}default:throw std::runtime_error("Bad JSON escape");}}}
        Json value(int depth=0){if(depth>128)throw std::runtime_error("JSON nesting limit");ws();if(i>=s.size())throw std::runtime_error("Empty JSON");char c=s[i];if(c=='"')return Json(text());if(c=='['||c=='{'){++i;Json v=c=='['?Json::array():Json::object();ws();if(i<s.size()&&s[i]==(c=='['?']':'}')){++i;return v;}for(;;){ws();if(c=='[')v.push(value(depth+1));else{auto k=text();ws();if(take()!=':')throw std::runtime_error("JSON colon expected");if(v.obj.count(k))throw std::runtime_error("Duplicate JSON key");v[k]=value(depth+1);}ws();char e=take();if(e==(c=='['?']':'}'))return v;if(e!=',')throw std::runtime_error("JSON comma expected");}}
            for(auto lit:{std::string("true"),std::string("false"),std::string("null")})if(s.compare(i,lit.size(),lit)==0){i+=lit.size();return lit=="null"?Json():Json(lit=="true");}
            size_t b=i;if(s[i]=='-')++i;if(i>=s.size()||!std::isdigit((unsigned char)s[i]))throw std::runtime_error("Bad JSON number");if(s[i]=='0')++i;else while(i<s.size()&&std::isdigit((unsigned char)s[i]))++i;
            if(i<s.size()&&s[i]=='.'){++i;size_t q=i;while(i<s.size()&&std::isdigit((unsigned char)s[i]))++i;if(q==i)throw std::runtime_error("Bad JSON fraction");}if(i<s.size()&&(s[i]=='e'||s[i]=='E')){++i;if(i<s.size()&&(s[i]=='+'||s[i]=='-'))++i;size_t q=i;while(i<s.size()&&std::isdigit((unsigned char)s[i]))++i;if(q==i)throw std::runtime_error("Bad JSON exponent");}double n=std::stod(s.substr(b,i-b));if(!std::isfinite(n))throw std::runtime_error("Nonfinite JSON");return Json(n);
        }
    };
    static Json parse(const std::string&s){Parser p{s};Json j=p.value();p.ws();if(p.i!=s.size())throw std::runtime_error("Trailing JSON data");return j;}
};
template<class T> Json numbers(const std::vector<T>& a){Json j=Json::array();for(auto v:a)j.push(Json(double(v)));return j;}
}
