#include <jni.h>
#include <unistd.h>
#include <link.h>
#include <set>
#include <map>
#include <functional>
#include <fcntl.h>
#include <dlfcn.h>
#include <sys/mman.h>
#include <cstdio>
#include <cstdint>
#include <thread>
#include <string>
#include <vector>
#include <sstream>
#include <cstring>
#include <cstdlib>
#include <map>
#include <mutex>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <array>
#include <utility>
#include <unwind.h>
#include <android/log.h>
#include <zlib.h>

#include "extern/Dobby/include/dobby.h"
#include "Include/BNMResolve.hpp"
#include "extern/BNM-Android/src/private/Internals.hpp"
#include "Include/httplib.h"
#include "explorer/html_page.hpp"
#include "explorer/luabnm.hpp"

#define LOGD(...) __android_log_print(ANDROID_LOG_DEBUG, "BNMExplorer", __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "BNMExplorer", __VA_ARGS__)

using namespace BNM;
using namespace BNM::IL2CPP;
using namespace BNM::Structures::Unity;

static int g_port = 8080;

static double nowSeconds() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

static void AttachThread() {
    BNM::AttachIl2Cpp();
}

static std::string jsEsc(const char* s) {
    if (!s) return "";
    std::string o;
    for (const char* p = s; *p; ++p) {
        unsigned char c = (unsigned char)*p;
        if      (c == '"')  o += "\\\"";
        else if (c == '\\') o += "\\\\";
        else if (c == '\n') o += "\\n";
        else if (c == '\r') o += "\\r";
        else if (c == '\t') o += "\\t";
        else if (c < 0x20) { char buf[8]; snprintf(buf, sizeof(buf), "\\u%04x", c); o += buf; }
        else                 o += *p;
    }
    return o;
}

static std::string jsEsc(const std::string& s) { return jsEsc(s.c_str()); }

static std::string typeName(const Il2CppType* t) {
    if (!t) return "?";
    Class c(t);
    if (!c.IsValid()) return "?";
    auto* r = c.GetClass();
    if (!r || !r->name) return "?";
    std::string n = r->name;
    if (r->namespaze && strlen(r->namespaze) > 0)
        n = std::string(r->namespaze) + "." + n;
    return n;
}

struct LoopEntry {
    std::string addr;
    std::string asm_;
    std::string ns;
    std::string cls;
    std::string name;
    std::string ftype;
    bool isProp;
    std::string val;
    int intervalMs;
    std::atomic<bool> active{true};
};

static std::mutex g_loopMtx;
static std::map<std::string, std::shared_ptr<LoopEntry>> g_loops;

struct LogEntry {
    std::string msg;
    int level;
    double timestamp;
};
static std::mutex g_logMtx;
static std::vector<LogEntry> g_logBuffer;
static const size_t LOG_MAX = 500;
static std::atomic<bool> g_logHooked{false};

static void addLogEntry(const std::string& msg, int level) {
    std::lock_guard<std::mutex> lk(g_logMtx);
    if (g_logBuffer.size() >= LOG_MAX) g_logBuffer.erase(g_logBuffer.begin());
    g_logBuffer.push_back({msg, level, nowSeconds()});
}

static void hookDebugLog() {
    if (g_logHooked.exchange(true)) return;
    try {
        Class debugCls("UnityEngine", "Debug");
        if (!debugCls.IsValid()) return;
    } catch(...) {}
}

struct CatcherArg {
    std::string type;
    std::string val;
};

struct CatcherCall {
    double ts;
    std::string instance;
    std::vector<CatcherArg> args;
    std::string ret;
};

struct CatcherEntry {
    std::string id;
    std::string asm_;
    std::string ns;
    std::string cls;
    std::string method;
    std::string retType;
    std::vector<std::string> paramTypes;
    std::vector<std::string> paramNames;
    std::atomic<bool> active{true};
    std::mutex callsMtx;
    std::vector<CatcherCall> calls;
    void* origPtr = nullptr;
    int slotIdx = -1;
    static const size_t MAX_CALLS = 100;
};

static std::mutex g_catcherMtx;
static std::map<std::string, std::shared_ptr<CatcherEntry>> g_catchers;

static std::string logsJson() {
    std::lock_guard<std::mutex> lk(g_logMtx);
    std::ostringstream j;
    j << "[";
    bool first = true;
    for (auto& e : g_logBuffer) {
        if (!first) j << ","; first = false;
        j << "{\"msg\":\"" << jsEsc(e.msg) << "\",\"level\":" << e.level << ",\"t\":" << std::fixed << e.timestamp << "}";
    }
    j << "]";
    return j.str();
}

static std::string jsonVal(const std::string& json, const std::string& key) {
    std::string qk = "\"" + key + "\":";
    auto pos = json.find(qk);
    if (pos == std::string::npos) return "";
    pos += qk.length();
    while (pos < json.length() && (json[pos] == ' ' || json[pos] == '\t')) pos++;
    if (pos < json.length() && json[pos] == '"') {
        pos++;
        size_t end = pos;
        bool esc = false;
        while (end < json.length()) {
            if (json[end] == '\\' && !esc) esc = true;
            else if (json[end] == '"' && !esc) break;
            else esc = false;
            end++;
        }
        if (end >= json.length()) return "";
        std::string raw = json.substr(pos, end - pos);
        std::string out;
        for (size_t i = 0; i < raw.length(); i++) {
            if (raw[i] == '\\' && i + 1 < raw.length()) {
                char n = raw[i+1];
                if      (n == '"')  { out += '"';  i++; }
                else if (n == '\\') { out += '\\'; i++; }
                else if (n == 'n')  { out += '\n'; i++; }
                else if (n == 'r')  { out += '\r'; i++; }
                else out += raw[i];
            } else out += raw[i];
        }
        return out;
    }
    auto end = json.find_first_of(",}", pos);
    if (end == std::string::npos) return "";
    std::string v = json.substr(pos, end - pos);
    while (!v.empty() && (v.back() == ' ' || v.back() == '\t' || v.back() == '\r' || v.back() == '\n')) v.pop_back();
    return v;
}

static MethodBase findObjsMethod() {
    static MethodBase m;
    if (m.IsValid()) return m;
    Class oc("UnityEngine", "Object");
    for (auto& mt : oc.GetMethods(false)) {
        auto* mi = mt.GetInfo();
        if (mi && mi->name && strcmp(mi->name, "FindObjectsOfType") == 0 && mi->parameters_count == 1)
            if (mi->parameters[0] && strstr(typeName(mi->parameters[0]).c_str(), "Type"))
            { m = mt; break; }
    }
    return m;
}

static Il2CppObject* sysTypeOf(const std::string& aqn) {
    Class tc("System", "Type", Image("mscorlib.dll"));
    if (!tc.IsValid()) return nullptr;
    Method<Il2CppObject*> gt(tc.GetMethod("GetType", 1));
    return gt.IsValid() ? gt(CreateMonoString(aqn.c_str())) : nullptr;
}

static bool isObjectAlive(Il2CppObject* obj) {
    if (!obj) return false;
    try {
        Method<bool> m(Class("UnityEngine", "Object").GetMethod("op_Implicit", 1));
        if (m.IsValid()) return m(obj);
        auto* k = obj->klass;
        if (!k || !k->name) return false;
        return true;
    } catch(...) { return false; }
}

static Il2CppObject* resolveComponentByAddr(uintptr_t addr) {
    if (!addr) return nullptr;
    Il2CppObject* obj = (Il2CppObject*)addr;
    if (!isObjectAlive(obj)) return nullptr;
    return obj;
}

static std::string assembliesJson() {
    std::ostringstream j;
    j << "[";
    bool first = true;
    for (auto& img : Image::GetImages()) {
        auto* d = img.GetInfo();
        if (!img.IsValid() || !d || !d->name) continue;
        if (!first) j << ",";
        first = false;
        j << "\"" << jsEsc(d->name) << "\"";
    }
    j << "]";
    return j.str();
}

static std::string classesJson(const std::string& asm_) {
    Image img(asm_);
    if (!img.IsValid()) return "[]";
    std::ostringstream j;
    j << "[";
    bool first = true;
    for (auto& cls : img.GetClasses(false)) {
        auto* r = cls.GetClass();
        if (!cls.IsValid() || !r || !r->name) continue;
        if (!first) j << ",";
        first = false;
        std::string t = "class";
        if (r->enumtype) t = "enum";
        else if (r->byval_arg.valuetype) t = "struct";
        else if (r->flags & 0x20) t = "interface";
        j << "{\"name\":\"" << jsEsc(r->name) << "\",\"ns\":\"" << jsEsc(r->namespaze ? r->namespaze : "") << "\",\"t\":\"" << t << "\"}";
    }
    j << "]";
    return j.str();
}

static std::string allClassesJson() {
    std::ostringstream j;
    j << "[";
    bool first = true;
    for (auto& img : Image::GetImages()) {
        auto* d = img.GetInfo();
        if (!img.IsValid() || !d || !d->name) continue;
        std::string a = d->name;
        for (auto& cls : img.GetClasses(false)) {
            auto* r = cls.GetClass();
            if (!cls.IsValid() || !r || !r->name) continue;
            if (!first) j << ",";
            first = false;
            std::string t = "class";
            if (r->enumtype) t = "enum";
            else if (r->byval_arg.valuetype) t = "struct";
            else if (r->flags & 0x20) t = "interface";
            j << "{\"name\":\"" << jsEsc(r->name) << "\",\"ns\":\"" << jsEsc(r->namespaze ? r->namespaze : "") << "\",\"t\":\"" << t << "\",\"a\":\"" << jsEsc(a) << "\"}";
        }
    }
    j << "]";
    return j.str();
}

static std::string classDetailJson(const std::string& a, const std::string& ns, const std::string& cn) {
    BNM_FileCheckpointF("classDetailJson: start a=%s ns=%s cn=%s", a.c_str(), ns.c_str(), cn.c_str());
    Class cls(ns.c_str(), cn.c_str(), Image(a.c_str()));
    BNM_FileCheckpoint("classDetailJson: Class constructed");
    if (!cls.IsValid()) { BNM_FileCheckpoint("classDetailJson: cls not valid, returning"); return "{}"; }
    BNM_FileCheckpoint("classDetailJson: cls valid, calling GetClass()");
    auto* r = cls.GetClass();
    BNM_FileCheckpointF("classDetailJson: GetClass() returned %p", (void*)r);
    if (!r) return "{}";

    std::ostringstream j;
    j << "{\"name\":\"" << jsEsc(cn) << "\",\"ns\":\"" << jsEsc(ns) << "\",\"asm\":\"" << jsEsc(a) << "\",";
    j << (r->parent && r->parent->name ? "\"parent\":\"" + jsEsc(r->parent->name) + "\"," : "\"parent\":null,");
    BNM_FileCheckpoint("classDetailJson: wrote header, calling GetFields()");

    j << "\"fields\":[";
    bool first = true;
    auto fields = cls.GetFields(false);
    BNM_FileCheckpointF("classDetailJson: GetFields() returned %zu fields", fields.size());
    for (auto& f : fields) {
        auto* fi = f.GetInfo();
        if (!f.IsValid() || !fi || !fi->name) continue;
        if (!first) j << ",";
        first = false;
        j << "{\"name\":\"" << jsEsc(fi->name) << "\",\"type\":\"" << jsEsc(typeName(fi->type)) << "\",\"s\":" << (fi->type && (fi->type->attrs & 0x10) ? "true" : "false") << ",\"off\":" << fi->offset << "}";
    }
    BNM_FileCheckpoint("classDetailJson: fields done, calling GetMethods()");

    j << "],\"methods\":[";
    first = true;
    auto methods = cls.GetMethods(false);
    BNM_FileCheckpointF("classDetailJson: GetMethods() returned %zu methods", methods.size());
    for (auto& m : methods) {
        auto* mi = m.GetInfo();
        if (!m.IsValid() || !mi || !mi->name) continue;
        if (!first) j << ",";
        first = false;
        char ab[32] = {};
        if (mi->methodPointer) snprintf(ab, sizeof(ab), "%llX", (unsigned long long)(uintptr_t)mi->methodPointer);
        j << "{\"name\":\"" << jsEsc(mi->name) << "\",\"ret\":\"" << jsEsc(typeName(mi->return_type)) << "\",\"s\":" << ((mi->flags & 0x10) ? "true" : "false") << ",\"addr\":\"" << ab << "\",\"params\":[";
        for (int i = 0; i < (int)mi->parameters_count; i++) {
            if (i > 0) j << ",";
            j << "{\"n\":\"arg" << i << "\",\"t\":\"" << jsEsc(mi->parameters && mi->parameters[i] ? typeName(mi->parameters[i]) : "?") << "\"}";
        }
        j << "]}";
    }

    j << "],\"props\":[";
    first = true;
    for (auto& p : cls.GetProperties(false)) {
        auto* pi = p._data;
        if (!p.IsValid() || !pi || !pi->name) continue;
        if (!first) j << ",";
        first = false;
        j << "{\"name\":\"" << jsEsc(pi->name) << "\",\"type\":\"" << jsEsc(pi->get && pi->get->return_type ? typeName(pi->get->return_type) : "?") << "\",\"g\":" << (pi->get ? "true" : "false") << ",\"s\":" << (pi->set ? "true" : "false") << "}";
    }
    j << "]}";
    return j.str();
}

struct InvokeResult { bool ok; std::string val, err; };

static InvokeResult invokeMethod(const std::string& body) {
    AttachThread();
    InvokeResult res = {false, "", ""};
    std::string a = jsonVal(body, "asm"), ns = jsonVal(body, "ns"), cn = jsonVal(body, "cls"), mn = jsonVal(body, "method");
    bool isStatic = jsonVal(body, "static") == "true" || jsonVal(body, "static") == "1";
    uintptr_t instAddr = (uintptr_t)strtoull(jsonVal(body, "instance").c_str(), nullptr, 16);

    std::vector<std::string> argT, argV;
    auto ap = body.find("\"args\":[");
    if (ap != std::string::npos) {
        ap += 8;
        auto ae = body.find("]", ap);
        if (ae != std::string::npos) {
            std::string ab = body.substr(ap, ae - ap);
            size_t p = 0;
            while (p < ab.size()) {
                auto ob = ab.find("{", p), cb = ab.find("}", ob);
                if (ob == std::string::npos || cb == std::string::npos) break;
                std::string e = "{" + ab.substr(ob + 1, cb - ob - 1) + "}";
                argT.push_back(jsonVal(e, "t"));
                argV.push_back(jsonVal(e, "v"));
                p = cb + 1;
            }
        }
    }

    if (a.empty() || cn.empty() || mn.empty()) { res.err = "Missing asm/cls/method"; return res; }

    Class cls(ns.c_str(), cn.c_str(), Image(a.c_str()));
    if (!cls.IsValid()) { res.err = "Class not found"; return res; }

    MethodBase mb = cls.GetMethod(mn.c_str(), (int)argT.size());
    if (!mb.IsValid()) mb = cls.GetMethod(mn.c_str());
    if (!mb.IsValid()) {
        for (Class cur = cls.GetParent(); cur.IsValid() && cur.GetClass() && !mb.IsValid(); cur = cur.GetParent()) {
            mb = cur.GetMethod(mn.c_str(), (int)argT.size());
            if (!mb.IsValid()) mb = cur.GetMethod(mn.c_str());
        }
    }
    if (!mb.IsValid()) { res.err = "Method not found"; return res; }
    auto* mi = mb.GetInfo();
    if (!mi || !mi->methodPointer) { res.err = "No pointer"; return res; }

    std::vector<void*> runtimeArgs;
    std::vector<int32_t> vi;
    std::vector<int64_t> vi64;
    std::vector<float> vf;
    std::vector<double> vd;
    std::vector<uint8_t> vb;
    std::vector<int16_t> vs16;
    std::vector<Vector3> vv3;
    std::vector<Vector2> vv2;
    std::vector<Color> vc;
    std::vector<Vector4> vv4;

    for (size_t i = 0; i < argT.size(); i++) {
        auto& t = argT[i]; auto& v = argV[i];
        if (t=="System.Int32"||t=="Int32"||t=="int") {
            vi.push_back(std::stoi(v.empty()?"0":v)); runtimeArgs.push_back(&vi.back());
        } else if (t=="System.Int64"||t=="Int64"||t=="long") {
            vi64.push_back(std::stoll(v.empty()?"0":v)); runtimeArgs.push_back(&vi64.back());
        } else if (t=="System.Single"||t=="Single"||t=="float") {
            vf.push_back(std::stof(v.empty()?"0":v)); runtimeArgs.push_back(&vf.back());
        } else if (t=="System.Double"||t=="Double"||t=="double") {
            vd.push_back(std::stod(v.empty()?"0":v)); runtimeArgs.push_back(&vd.back());
        } else if (t=="System.Boolean"||t=="Boolean"||t=="bool") {
            vb.push_back((uint8_t)(v=="true"||v=="1"?1:0)); runtimeArgs.push_back(&vb.back());
        } else if (t=="System.Byte"||t=="Byte"||t=="byte") {
            vb.push_back((uint8_t)std::stoul(v.empty()?"0":v)); runtimeArgs.push_back(&vb.back());
        } else if (t=="System.Int16"||t=="Int16"||t=="short") {
            vs16.push_back((int16_t)std::stoi(v.empty()?"0":v)); runtimeArgs.push_back(&vs16.back());
        } else if (t=="UnityEngine.Vector3"||t=="Vector3") {
            float x=0,y=0,z=0; sscanf(v.c_str(),"[%f,%f,%f]",&x,&y,&z); vv3.push_back({x,y,z}); runtimeArgs.push_back(&vv3.back());
        } else if (t=="UnityEngine.Vector2"||t=="Vector2") {
            float x=0,y=0; sscanf(v.c_str(),"[%f,%f]",&x,&y); vv2.push_back({x,y}); runtimeArgs.push_back(&vv2.back());
        } else if (t=="UnityEngine.Color"||t=="Color") {
            float r=1,g=1,b=1,aa=1; sscanf(v.c_str(),"[%f,%f,%f,%f]",&r,&g,&b,&aa); vc.push_back({r,g,b,aa}); runtimeArgs.push_back(&vc.back());
        } else if (t=="UnityEngine.Vector4"||t=="Vector4"||t=="UnityEngine.Quaternion"||t=="Quaternion") {
            float x=0,y=0,z=0,w=0; sscanf(v.c_str(),"[%f,%f,%f,%f]",&x,&y,&z,&w); vv4.push_back({x,y,z,w}); runtimeArgs.push_back(&vv4.back());
        } else if (t=="System.String"||t=="String"||t=="string") {
            runtimeArgs.push_back(CreateMonoString(v));
        } else {
            runtimeArgs.push_back((void*)strtoull(v.empty()?"0":v.c_str(), nullptr, 16));
        }
    }

    void* inst = isStatic ? nullptr : (void*)instAddr;
    std::string rtn = typeName(mi->return_type);
    std::ostringstream out;

    Il2CppException* exc = nullptr;
    void* ret = BNM::Internal::il2cppMethods.il2cpp_runtime_invoke(mi, inst, runtimeArgs.empty() ? nullptr : runtimeArgs.data(), &exc);

    if (exc) {
        BNM::Structures::Mono::String* excMsg = nullptr;
        try {
            auto excCls = Class(((Il2CppObject*)exc)->klass);
            if (excCls.IsValid()) {
                auto msgMethod = excCls.GetMethod("get_Message", 0);
                if (msgMethod.IsValid()) {
                    auto* msgMi = msgMethod.GetInfo();
                    if (msgMi && msgMi->methodPointer) {
                        Il2CppException* inner = nullptr;
                        auto* msgRet = (Il2CppObject*)BNM::Internal::il2cppMethods.il2cpp_runtime_invoke(msgMi, exc, nullptr, &inner);
                        if (msgRet && !inner) excMsg = (BNM::Structures::Mono::String*)msgRet;
                    }
                }
            }
        } catch(...) {}
        res.err = excMsg ? excMsg->str() : "IL2CPP exception";
        return res;
    }

    if (rtn=="System.Void"||rtn=="Void"||rtn=="void"||rtn=="?") {
        out << "void";
    } else if (rtn=="System.Single"||rtn=="Single"||rtn=="float") {
        float v = ret ? *(float*)((uint8_t*)ret + sizeof(Il2CppObject)) : 0;
        out << v;
    } else if (rtn=="System.Double"||rtn=="Double"||rtn=="double") {
        double v = ret ? *(double*)((uint8_t*)ret + sizeof(Il2CppObject)) : 0;
        out << v;
    } else if (rtn=="System.Int32"||rtn=="Int32"||rtn=="int") {
        int v = ret ? *(int*)((uint8_t*)ret + sizeof(Il2CppObject)) : 0;
        out << v;
    } else if (rtn=="System.Int64"||rtn=="Int64"||rtn=="long") {
        int64_t v = ret ? *(int64_t*)((uint8_t*)ret + sizeof(Il2CppObject)) : 0;
        out << v;
    } else if (rtn=="System.Boolean"||rtn=="Boolean"||rtn=="bool") {
        bool v = ret ? *(bool*)((uint8_t*)ret + sizeof(Il2CppObject)) : false;
        out << (v?"true":"false");
    } else if (rtn=="System.Byte"||rtn=="Byte"||rtn=="byte") {
        uint8_t v = ret ? *(uint8_t*)((uint8_t*)ret + sizeof(Il2CppObject)) : 0;
        out << (int)v;
    } else if (rtn=="UnityEngine.Vector3"||rtn=="Vector3") {
        Vector3 v = ret ? *(Vector3*)((uint8_t*)ret + sizeof(Il2CppObject)) : Vector3{0,0,0};
        char buf[128]; snprintf(buf,sizeof(buf),"[%.4f, %.4f, %.4f]",v.x,v.y,v.z); out<<buf;
    } else if (rtn=="UnityEngine.Vector2"||rtn=="Vector2") {
        Vector2 v = ret ? *(Vector2*)((uint8_t*)ret + sizeof(Il2CppObject)) : Vector2{0,0};
        char buf[128]; snprintf(buf,sizeof(buf),"[%.4f, %.4f]",v.x,v.y); out<<buf;
    } else if (rtn=="UnityEngine.Color"||rtn=="Color") {
        Color v = ret ? *(Color*)((uint8_t*)ret + sizeof(Il2CppObject)) : Color{0,0,0,0};
        char buf[128]; snprintf(buf,sizeof(buf),"[%.4f, %.4f, %.4f, %.4f]",v.r,v.g,v.b,v.a); out<<buf;
    } else if (rtn=="System.String"||rtn=="String"||rtn=="string") {
        auto* s = (BNM::Structures::Mono::String*)ret;
        out << (s ? s->str() : "null");
    } else {
        char buf[64]; snprintf(buf,sizeof(buf),"0x%llX",(unsigned long long)(uintptr_t)ret);
        out << rtn << " @ " << buf;
    }
    res.ok = true; res.val = out.str();
    return res;
}

static std::string instancesJson(const std::string& a, const std::string& ns, const std::string& cn) {
    AttachThread();
    std::string full = ns.empty() ? cn : ns + "." + cn;
    std::string aNoExt = a;
    auto dp = aNoExt.find(".dll");
    if (dp != std::string::npos) aNoExt = aNoExt.substr(0, dp);

    Il2CppObject* st = sysTypeOf(full + ", " + aNoExt);
    if (!st) st = sysTypeOf(full);
    if (!st) return "{\"error\":\"Class not found / System.Type missing\"}";

    auto fm = findObjsMethod();
    if (!fm.IsValid()) return "{\"error\":\"FindObjectsOfType missing\"}";

    std::ostringstream j;
    j << "{\"instances\":[";
    try {
        auto* arr = Method<Array<Il2CppObject*>*>(fm)(st);
        if (arr && arr->capacity > 0) {
            bool first = true;
            Method<BNM::Structures::Mono::String*> gn(Class("UnityEngine","Object").GetMethod("get_name"));
            for (int i = 0; i < arr->capacity; i++) {
                Il2CppObject* obj = arr->m_Items[i];
                if (!obj) continue;
                if (!first) j << ",";
                first = false;
                std::string name = "obj";
                if (gn.IsValid()) { auto* s = gn[obj](); if (s) name = s->str(); }
                char addr[32]; snprintf(addr, sizeof(addr), "%llX", (unsigned long long)(uintptr_t)obj);
                j << "{\"addr\":\"" << addr << "\",\"name\":\"" << jsEsc(name) << "\"}";
            }
        }
    } catch(...) {}
    j << "]}";
    return j.str();
}

static std::string sceneJson() {
    AttachThread();
    auto fm = findObjsMethod();
    if (!fm.IsValid()) return "[]";

    Il2CppObject* st = sysTypeOf("UnityEngine.GameObject, UnityEngine.CoreModule");
    if (!st) st = sysTypeOf("UnityEngine.GameObject, UnityEngine");
    if (!st) st = sysTypeOf("UnityEngine.GameObject");
    if (!st) return "[]";

    Method<BNM::Structures::Mono::String*> gn(Class("UnityEngine","Object").GetMethod("get_name"));
    Method<bool> ga(Class("UnityEngine","GameObject").GetMethod("get_activeSelf"));
    Method<Il2CppObject*> gt(Class("UnityEngine","GameObject").GetMethod("get_transform"));
    Method<Il2CppObject*> tp(Class("UnityEngine","Transform").GetMethod("get_parent"));
    Method<Il2CppObject*> cg(Class("UnityEngine","Component").GetMethod("get_gameObject"));

    std::ostringstream j;
    j << "[";
    try {
        auto* arr = Method<Array<Il2CppObject*>*>(fm)(st);
        if (arr && arr->capacity > 0) {
            bool first = true;
            for (int i = 0; i < arr->capacity; i++) {
                Il2CppObject* go = arr->m_Items[i];
                if (!go) continue;
                std::string name = "Unknown";
                bool active = false;
                if (gn.IsValid()) { auto* s = gn[go](); if (s) name = s->str(); }
                if (ga.IsValid()) active = ga[go]();
                uintptr_t par = 0;
                if (gt.IsValid() && tp.IsValid() && cg.IsValid()) {
                    Il2CppObject* tr = gt[go]();
                    if (tr) { Il2CppObject* pt = tp[tr](); if (pt) { Il2CppObject* pg = cg[pt](); if (pg) par = (uintptr_t)pg; } }
                }
                if (!first) j << ",";
                first = false;
                j << "{\"addr\":\"" << std::hex << (uintptr_t)go << "\",\"name\":\"" << jsEsc(name) << "\",\"active\":" << (active?"true":"false") << ",\"parent\":\"" << std::hex << par << "\"}";
            }
        }
    } catch(...) {}
    j << "]";
    return j.str();
}

static std::string readField(const std::string& ft, Class& cls, Il2CppObject* inst, const std::string& name, MethodBase* getter, bool isStatic, bool& ok) {
    ok = false;
    if (!inst && !isStatic) return "";
    try {
        if (ft=="System.Single"||ft=="Single"||ft=="float") {
            float v = 0;
            if (getter) { Method<float> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<float> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.Double"||ft=="Double"||ft=="double") {
            double v = 0;
            if (getter) { Method<double> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<double> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.Int32"||ft=="Int32"||ft=="int") {
            int v = 0;
            if (getter) { Method<int> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<int> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.UInt32"||ft=="UInt32"||ft=="uint") {
            uint32_t v = 0;
            if (getter) { Method<uint32_t> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<uint32_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.Int64"||ft=="Int64"||ft=="long") {
            int64_t v = 0;
            if (getter) { Method<int64_t> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<int64_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.UInt64"||ft=="UInt64"||ft=="ulong") {
            uint64_t v = 0;
            if (getter) { Method<uint64_t> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<uint64_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.Int16"||ft=="Int16"||ft=="short") {
            int16_t v = 0;
            if (getter) { Method<int16_t> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<int16_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.UInt16"||ft=="UInt16"||ft=="ushort") {
            uint16_t v = 0;
            if (getter) { Method<uint16_t> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<uint16_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.Byte"||ft=="Byte"||ft=="byte") {
            uint8_t v = 0;
            if (getter) { Method<uint8_t> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<uint8_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.SByte"||ft=="SByte"||ft=="sbyte") {
            int8_t v = 0;
            if (getter) { Method<int8_t> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<int8_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="System.Boolean"||ft=="Boolean"||ft=="bool") {
            bool v = false;
            if (getter) { Method<bool> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<bool> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return v ? "true" : "false";
        } else if (ft=="System.String"||ft=="String"||ft=="string") {
            BNM::Structures::Mono::String* s = nullptr;
            if (getter) { Method<BNM::Structures::Mono::String*> m(*getter); if(!isStatic) m.SetInstance(inst); s = m(); }
            else { Field<BNM::Structures::Mono::String*> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);s=f();} }
            ok = true; return s ? "\"" + jsEsc(s->str()) + "\"" : "\"\"";
        } else if (ft=="UnityEngine.Vector3"||ft=="Vector3") {
            Vector3 v = {0,0,0};
            if (getter) { Method<Vector3> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<Vector3> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return "[" + std::to_string(v.x) + "," + std::to_string(v.y) + "," + std::to_string(v.z) + "]";
        } else if (ft=="UnityEngine.Color"||ft=="Color") {
            Color c = {0,0,0,0};
            if (getter) { Method<Color> m(*getter); if(!isStatic) m.SetInstance(inst); c = m(); }
            else { Field<Color> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);c=f();} }
            ok = true; return "[" + std::to_string(c.r) + "," + std::to_string(c.g) + "," + std::to_string(c.b) + "," + std::to_string(c.a) + "]";
        } else if (ft=="UnityEngine.Vector2"||ft=="Vector2") {
            Vector2 v = {0,0};
            if (getter) { Method<Vector2> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<Vector2> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return "[" + std::to_string(v.x) + "," + std::to_string(v.y) + "]";
        } else if (ft=="UnityEngine.Vector4"||ft=="Vector4"||ft=="UnityEngine.Quaternion"||ft=="Quaternion") {
            Vector4 v = {0,0,0,0};
            if (getter) { Method<Vector4> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<Vector4> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::string("{\"x\":") + std::to_string(v.x) + ",\"y\":" + std::to_string(v.y) + ",\"z\":" + std::to_string(v.z) + ",\"w\":" + std::to_string(v.w) + "}";
        } else if (ft=="UnityEngine.LayerMask"||ft=="LayerMask") {
            int v = 0;
            if (getter) { Method<int> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<int> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true; return std::to_string(v);
        } else if (ft=="UnityEngine.Rect"||ft=="Rect") {
            Rect v = {0,0,0,0};
            if (getter) { Method<Rect> m(*getter); if(!isStatic) m.SetInstance(inst); v = m(); }
            else { Field<Rect> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);v=f();} }
            ok = true;
            return std::string("{\"x\":") + std::to_string(v.x) + ",\"y\":" + std::to_string(v.y) + ",\"w\":" + std::to_string(v.w) + ",\"h\":" + std::to_string(v.h) + "}";
        }
    } catch(...) {}
    return "";
}

static void writeField(const std::string& ft, Class& cls, Il2CppObject* inst, const std::string& name, MethodBase* setter, const std::string& v, bool isStatic = false) {
    if (!inst && !isStatic) return;
    try {
        if (ft=="System.Single"||ft=="Single"||ft=="float") {
            float val = std::stof(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<float> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.Double"||ft=="Double"||ft=="double") {
            double val = std::stod(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<double> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.Int32"||ft=="Int32"||ft=="int") {
            int val = std::stoi(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<int> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.UInt32"||ft=="UInt32"||ft=="uint") {
            uint32_t val = (uint32_t)std::stoul(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<uint32_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.Int64"||ft=="Int64"||ft=="long") {
            int64_t val = std::stoll(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<int64_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.UInt64"||ft=="UInt64"||ft=="ulong") {
            uint64_t val = std::stoull(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<uint64_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.Int16"||ft=="Int16"||ft=="short") {
            int16_t val = (int16_t)std::stoi(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<int16_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.UInt16"||ft=="UInt16"||ft=="ushort") {
            uint16_t val = (uint16_t)std::stoul(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<uint16_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.Byte"||ft=="Byte"||ft=="byte") {
            uint8_t val = (uint8_t)std::stoul(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<uint8_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.SByte"||ft=="SByte"||ft=="sbyte") {
            int8_t val = (int8_t)std::stoi(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<int8_t> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.Boolean"||ft=="Boolean"||ft=="bool") {
            bool val = v=="true"||v=="1";
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<bool> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="System.String"||ft=="String"||ft=="string") {
            auto* s = CreateMonoString(v.c_str());
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(s); }
            else { Field<BNM::Structures::Mono::String*> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=s;} }
        } else if (ft=="UnityEngine.Vector3"||ft=="Vector3") {
            float x=0,y=0,z=0; sscanf(v.c_str(),"[%f,%f,%f]",&x,&y,&z); Vector3 val={x,y,z};
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<Vector3> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="UnityEngine.Color"||ft=="Color") {
            float r=1,g=1,b=1,a=1; sscanf(v.c_str(),"[%f,%f,%f,%f]",&r,&g,&b,&a); Color val={r,g,b,a};
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<Color> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="UnityEngine.Vector2"||ft=="Vector2") {
            float x=0,y=0; sscanf(v.c_str(),"[%f,%f]",&x,&y); Vector2 val={x,y};
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<Vector2> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="UnityEngine.Vector4"||ft=="Vector4"||ft=="UnityEngine.Quaternion"||ft=="Quaternion") {
            float x=0,y=0,z=0,w=0;
            sscanf(v.c_str(),"{\"x\":%f,\"y\":%f,\"z\":%f,\"w\":%f}",&x,&y,&z,&w);
            Vector4 val={x,y,z,w};
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<Vector4> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="UnityEngine.LayerMask"||ft=="LayerMask") {
            int val = std::stoi(v);
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<int> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        } else if (ft=="UnityEngine.Rect"||ft=="Rect") {
            float x=0,y=0,w=0,h=0;
            sscanf(v.c_str(),"{\"x\":%f,\"y\":%f,\"w\":%f,\"h\":%f}",&x,&y,&w,&h);
            Rect val={x,y,w,h};
            if (setter) { Method<void> m(*setter); if(!isStatic) m.SetInstance(inst); m(val); }
            else { Field<Rect> f=cls.GetField(name.c_str()); if(f.IsValid()){if(!isStatic)f.SetInstance(inst);f=val;} }
        }
    } catch(...) {}
}

static std::string goInfoJson(uintptr_t addr) {
    AttachThread();
    if (!addr) return "{}";
    Il2CppObject* go = (Il2CppObject*)addr;
    if (!isObjectAlive(go)) return "{\"stale\":true}";
    std::string name = "Unknown";
    bool active = false;

    Class goCls("UnityEngine","GameObject");
    Method<BNM::Structures::Mono::String*> gn(Class("UnityEngine","Object").GetMethod("get_name"));
    Method<bool> ga(goCls.GetMethod("get_activeSelf"));
    Method<Il2CppObject*> gt(goCls.GetMethod("get_transform"));

    try {
        if (gn.IsValid()) { auto* s=gn[go](); if(s) name=s->str(); }
        if (ga.IsValid()) active = ga[go]();
    } catch(...) {}

    std::ostringstream j;
    j << "{\"addr\":\"" << std::hex << addr << "\",\"name\":\"" << jsEsc(name) << "\",\"active\":" << (active?"true":"false") << ",\"transform\":{";
    try {
        if (gt.IsValid()) {
            Il2CppObject* tr = gt[go]();
            if (tr) {
                Method<Vector3> gp(Class("UnityEngine","Transform").GetMethod("get_localPosition"));
                Method<Vector3> gr(Class("UnityEngine","Transform").GetMethod("get_localEulerAngles"));
                Method<Vector3> gs(Class("UnityEngine","Transform").GetMethod("get_localScale"));
                Vector3 p=gp.IsValid()?gp[tr]():Vector3{0,0,0};
                Vector3 r=gr.IsValid()?gr[tr]():Vector3{0,0,0};
                Vector3 s=gs.IsValid()?gs[tr]():Vector3{0,0,0};
                j << "\"addr\":\"" << std::hex << (uintptr_t)tr << "\",\"p\":[" << p.x << "," << p.y << "," << p.z << "],\"r\":[" << r.x << "," << r.y << "," << r.z << "],\"s\":[" << s.x << "," << s.y << "," << s.z << "]";
            }
        }
    } catch(...) {}
    j << "},\"scripts\":[";

    try {
        Class componentCls("UnityEngine", "Component");
        Il2CppObject* compReflType = nullptr;
        if (componentCls.IsValid()) compReflType = (Il2CppObject*)componentCls.GetMonoType();
        if (!compReflType) compReflType = sysTypeOf("UnityEngine.Component, UnityEngine.CoreModule");
        if (!compReflType) compReflType = sysTypeOf("UnityEngine.Component");
        Array<Il2CppObject*>* comps = nullptr;
        if (!comps && compReflType) {
            for (auto& mt : goCls.GetMethods(true)) {
                auto* mi = mt.GetInfo();
                if (!mi || !mi->name || mi->is_generic) continue;
                if (strcmp(mi->name, "GetComponents") != 0) continue;
                if (mi->parameters_count == 1) {
                    try {
                        Method<Array<Il2CppObject*>*> m(mt);
                        comps = m[go](compReflType);
                    } catch(...) { LOGE("goInfoJson: GetComponents(Type) threw"); }
                    if (comps && comps->capacity > 0) break;
                    comps = nullptr;
                }
            }
        }
        if (!comps && compReflType) {
            for (auto& mt : goCls.GetMethods(true)) {
                auto* mi = mt.GetInfo();
                if (!mi || !mi->name) continue;
                if (strcmp(mi->name, "GetComponentsInternal") != 0) continue;
                LOGD("goInfoJson: found GetComponentsInternal with %d params", mi->parameters_count);
                if (mi->parameters_count >= 2) {
                    try {
                        if (mi->parameters_count == 6) {
                            Method<Array<Il2CppObject*>*> m(mt);
                            comps = m[go](compReflType, true, false, true, false, nullptr);
                            LOGD("goInfoJson: GetComponentsInternal(6) returned %p, count=%d", comps, comps ? (int)comps->capacity : -1);
                        } else if (mi->parameters_count == 5) {
                            Method<Array<Il2CppObject*>*> m(mt);
                            comps = m[go](compReflType, true, false, true, false);
                            LOGD("goInfoJson: GetComponentsInternal(5) returned %p, count=%d", comps, comps ? (int)comps->capacity : -1);
                        } else if (mi->parameters_count == 4) {
                            Method<Array<Il2CppObject*>*> m(mt);
                            comps = m[go](compReflType, false, true, false);
                            LOGD("goInfoJson: GetComponentsInternal(4) returned %p, count=%d", comps, comps ? (int)comps->capacity : -1);
                        }
                    } catch(...) { LOGE("goInfoJson: GetComponentsInternal threw"); }
                    if (comps && comps->capacity > 0) break;
                    comps = nullptr;
                }
            }
        }
        if (!comps) {
            LOGD("goInfoJson: falling back to FindObjectsOfType");
            Class monoCls("UnityEngine", "MonoBehaviour");
            Il2CppObject* monoType = nullptr;
            if (monoCls.IsValid()) monoType = (Il2CppObject*)monoCls.GetMonoType();
            if (!monoType) monoType = sysTypeOf("UnityEngine.MonoBehaviour, UnityEngine.CoreModule");
            if (!monoType) monoType = sysTypeOf("UnityEngine.MonoBehaviour");

            auto fom = findObjsMethod();
            Method<Il2CppObject*> compGetGO(Class("UnityEngine","Component").GetMethod("get_gameObject"));

            if (fom.IsValid() && monoType && compGetGO.IsValid()) {
                auto* allMonos = Method<Array<Il2CppObject*>*>(fom)(monoType);
                LOGD("goInfoJson: FindObjectsOfType(MonoBehaviour) count=%d", allMonos ? (int)allMonos->capacity : -1);
                if (allMonos && allMonos->capacity > 0) {
                    for (int i = 0; i < allMonos->capacity; i++) {
                        Il2CppObject* c = allMonos->m_Items[i];
                        if (!c) continue;
                        try {
                            Il2CppObject* cgo = compGetGO[c]();
                            if ((uintptr_t)cgo != addr) allMonos->m_Items[i] = nullptr;
                        } catch(...) { allMonos->m_Items[i] = nullptr; }
                    }
                    comps = allMonos;
                }
            }
        }

        LOGD("goInfoJson: final comps=%p, count=%d", comps, comps ? (int)comps->capacity : -1);

        static const char* kStopAtComponent[] = {
                "Component", "Object", nullptr
        };
        static const char* kSkipProps[] = {
                "isActiveAndEnabled", "transform", "gameObject", "tag", "name",
                "hideFlags", "rigidbody", "rigidbody2D", "camera", "light",
                "animation", "constantForce", "renderer", "audio", "networkView",
                "collider", "collider2D", "hingeJoint", "particleSystem", nullptr
        };

        auto isStopClass = [](const char* n) -> bool {
            for (auto** s = kStopAtComponent; *s; s++)
                if (strcmp(n, *s) == 0) return true;
            return false;
        };
        auto isSkipProp = [](const char* n) -> bool {
            for (auto** s = kSkipProps; *s; s++)
                if (strcmp(n, *s) == 0) return true;
            return false;
        };

        auto isKnownBuiltinBase = [](const char* n) -> bool {
            static const char* builtins[] = {
                    "MonoBehaviour", "Behaviour",
                    "Collider", "Collider2D",
                    "BoxCollider", "SphereCollider", "CapsuleCollider", "MeshCollider", "TerrainCollider", "WheelCollider",
                    "BoxCollider2D", "CircleCollider2D", "PolygonCollider2D", "EdgeCollider2D", "CapsuleCollider2D", "CompositeCollider2D",
                    "Rigidbody", "Rigidbody2D",
                    "Renderer", "MeshRenderer", "SkinnedMeshRenderer", "SpriteRenderer", "LineRenderer", "TrailRenderer", "ParticleSystemRenderer",
                    "MeshFilter",
                    "Animator", "Animation",
                    "AudioSource", "AudioListener",
                    "Camera", "Light", "LensFlare", "Projector",
                    "Canvas", "CanvasGroup", "CanvasRenderer",
                    "RectTransform",
                    "Text", "Image", "RawImage", "Button", "Toggle", "Slider", "Scrollbar", "Dropdown", "InputField", "ScrollRect", "Mask", "RectMask2D",
                    "TMP_Text", "TextMeshPro", "TextMeshProUGUI",
                    "NavMeshAgent", "NavMeshObstacle",
                    "ParticleSystem",
                    "Joint", "HingeJoint", "FixedJoint", "SpringJoint", "CharacterJoint", "ConfigurableJoint",
                    "Joint2D", "HingeJoint2D", "FixedJoint2D", "SpringJoint2D", "WheelJoint2D", "SliderJoint2D",
                    "CharacterController",
                    "LODGroup",
                    "OcclusionArea", "OcclusionPortal",
                    "NetworkView",
                    "ConstantForce",
                    nullptr
            };
            for (auto** s = builtins; *s; s++)
                if (strcmp(n, *s) == 0) return true;
            return false;
        };

        if (comps && comps->capacity > 0) {
            bool firstScript = true;

            for (int i = 0; i < comps->capacity; i++) {
                Il2CppObject* comp = comps->m_Items[i];
                if (!comp || !comp->klass) continue;

                auto* klass = comp->klass;

                bool isSupported = false;
                std::string compCategory = "script";
                for (auto* cur = klass; cur; cur = cur->parent) {
                    if (!cur->name) continue;
                    if (strcmp(cur->name, "MonoBehaviour") == 0) { isSupported = true; compCategory = "script"; break; }
                    if (isKnownBuiltinBase(cur->name) && strcmp(cur->name, "MonoBehaviour") != 0 && strcmp(cur->name, "Behaviour") != 0) {
                        isSupported = true; compCategory = "builtin"; break;
                    }
                    if (strcmp(cur->name, "Component") == 0 || strcmp(cur->name, "Object") == 0) break;
                }
                if (!isSupported) continue;

                std::string compName = klass->name ? klass->name : "Unknown";
                std::string compNs = klass->namespaze ? klass->namespaze : "";
                char compAddr[32]; snprintf(compAddr, sizeof(compAddr), "%llX", (unsigned long long)(uintptr_t)comp);
                std::string compAsm = (klass->image && klass->image->name) ? klass->image->name : "";

                bool hasEnabledProp = false;
                bool enabled = false;
                for (auto* cur = klass; cur; cur = cur->parent) {
                    if (!cur->name) continue;
                    if (strcmp(cur->name, "Behaviour") == 0 || strcmp(cur->name, "Renderer") == 0 ||
                        strcmp(cur->name, "Collider") == 0 || strcmp(cur->name, "Collider2D") == 0) {
                        hasEnabledProp = true; break;
                    }
                    if (strcmp(cur->name, "Component") == 0) break;
                }
                if (hasEnabledProp) {
                    Method<bool> getEnabled(Class("UnityEngine","Behaviour").GetMethod("get_enabled"));
                    if (!getEnabled.IsValid()) {
                        Class compClsCheck(klass);
                        auto ep = compClsCheck.GetProperty("enabled");
                        if (ep.IsValid() && ep._data && ep._data->get) {
                            MethodBase egetter(ep._data->get);
                            Method<bool> m(egetter); m.SetInstance(comp);
                            try { enabled = m(); } catch(...) {}
                        }
                    } else {
                        try { enabled = getEnabled[comp](); } catch(...) {}
                    }
                }

                if (!firstScript) j << ",";
                firstScript = false;
                j << "{\"addr\":\"" << compAddr << "\",\"name\":\"" << jsEsc(compName) << "\",\"ns\":\"" << jsEsc(compNs) << "\",\"asm\":\"" << jsEsc(compAsm) << "\",\"category\":\"" << compCategory << "\",\"enabled\":" << (enabled?"true":"false") << ",\"fields\":[";

                LOGD("goInfoJson: found component '%s' category='%s' at %s", compName.c_str(), compCategory.c_str(), compAddr);

                try {
                    Class compCls(klass);
                    bool firstF = true;
                    std::vector<std::string> seen;

                    for (Class cur = compCls; cur.IsValid() && cur.GetClass(); cur = cur.GetParent()) {
                        auto* cn_ = cur.GetClass()->name;
                        if (!cn_) break;
                        if (isStopClass(cn_)) break;

                        for (auto& f : cur.GetFields(false)) {
                            try {
                                auto* fi = f.GetInfo();
                                if (!fi || !fi->type || (fi->type->attrs & 0x10)) continue;
                                std::string fn = fi->name;
                                bool dup = false; for (auto& s : seen) if (s==fn) dup=true;
                                if (dup) continue; seen.push_back(fn);
                                std::string ft = typeName(fi->type);
                                bool ok = false;
                                std::string vs = readField(ft, cur, comp, fn, nullptr, false, ok);
                                if (ok) {
                                    if (!firstF) j << ","; firstF = false;
                                    j << "{\"name\":\"" << jsEsc(fn) << "\",\"type\":\"" << jsEsc(ft) << "\",\"val\":" << vs << ",\"isProp\":false,\"canWrite\":true}";
                                }
                            } catch(...) {}
                        }

                        for (auto& p : cur.GetProperties(false)) {
                            try {
                                auto* pi = p._data;
                                if (!pi || !pi->get) continue;
                                std::string pn = pi->name;
                                if (isSkipProp(pn.c_str())) continue;
                                bool dup = false; for (auto& s : seen) if (s==pn) dup=true;
                                if (dup) continue; seen.push_back(pn);
                                std::string pt = typeName(pi->get->return_type);
                                bool ok = false;
                                MethodBase getter(pi->get);
                                std::string vs = readField(pt, cur, comp, pn, &getter, false, ok);
                                if (ok) {
                                    if (!firstF) j << ","; firstF = false;
                                    j << "{\"name\":\"" << jsEsc(pn) << "\",\"type\":\"" << jsEsc(pt) << "\",\"val\":" << vs << ",\"isProp\":true,\"canWrite\":" << (pi->set?"true":"false") << "}";
                                }
                            } catch(...) {}
                        }
                    }
                } catch(...) {}
                j << "]}";
            }
        }
    } catch(...) {
        LOGE("goInfoJson: exception in script enumeration");
    }
    j << "]}";
    return j.str();
}

// GameManager can't be edited from the explorer (131b18, owner 2026-10-11): it's the
// class players used to turn on creative mode and break maps. Everything else stays writable.
static bool isBlockedClass(const std::string& clsName) {
    std::string low;
    low.reserve(clsName.size());
    for (char c : clsName) low.push_back((char)tolower((unsigned char)c));
    return low.find("gamemanager") != std::string::npos;
}

static void applyLoopEntry(const std::shared_ptr<LoopEntry>& e) {
    if (isBlockedClass(e->cls)) return;   // GameManager is locked (131b18)
    try {
        uintptr_t addr = (uintptr_t)strtoull(e->addr.c_str(), nullptr, 16);
        if (!addr) return;
        Il2CppObject* obj = resolveComponentByAddr(addr);
        if (!obj) { e->active = false; return; }
        for (Class cur(e->ns.c_str(), e->cls.c_str(), Image(e->asm_.c_str())); cur.IsValid(); cur = cur.GetParent()) {
            if (e->isProp) {
                auto p = cur.GetProperty(e->name.c_str());
                if (p.IsValid() && p._data && p._data->set) {
                    MethodBase s(p._data->set);
                    writeField(e->ftype, cur, obj, e->name, &s, e->val);
                    return;
                }
            } else {
                auto f = cur.GetField(e->name.c_str());
                if (f.IsValid()) { writeField(e->ftype, cur, obj, e->name, nullptr, e->val); return; }
            }
        }
    } catch(...) {}
}

static std::string loopListJson() {
    std::lock_guard<std::mutex> lk(g_loopMtx);
    std::ostringstream j;
    j << "[";
    bool first = true;
    for (auto& kv : g_loops) {
        if (!first) j << ","; first = false;
        auto& e = kv.second;
        j << "{\"id\":\"" << jsEsc(kv.first) << "\",\"cls\":\"" << jsEsc(e->cls) << "\",\"name\":\"" << jsEsc(e->name) << "\",\"val\":\"" << jsEsc(e->val) << "\",\"interval\":" << e->intervalMs << ",\"active\":" << (e->active?"true":"false") << "}";
    }
    j << "]";
    return j.str();
}

#define MAX_CATCHERS 64
static std::shared_ptr<CatcherEntry>* g_catcherSlots[MAX_CATCHERS] = {};
static int g_catcherSlotCount = 0;
static std::mutex g_catcherSlotMtx;
static void (*g_catcherOrigPtrs[MAX_CATCHERS])(Il2CppObject*) = {};

template<int N>
static void catcherHook(Il2CppObject* self) {
    std::shared_ptr<CatcherEntry>* slot = g_catcherSlots[N];
    if (slot && *slot && (*slot)->active) {
        CatcherCall call;
        call.ts = nowSeconds();
        char buf[32]; snprintf(buf, sizeof(buf), "0x%llX", (unsigned long long)(uintptr_t)self);
        call.instance = buf;
        if (self && self->klass) {
            try {
                Method<BNM::Structures::Mono::String*> gn(Class("UnityEngine","Object").GetMethod("get_name"));
                if (gn.IsValid()) { auto* s = gn[self](); if (s) call.instance += " (" + s->str() + ")"; }
            } catch(...) {}
        }
        std::lock_guard<std::mutex> lk((*slot)->callsMtx);
        if ((*slot)->calls.size() >= CatcherEntry::MAX_CALLS) (*slot)->calls.erase((*slot)->calls.begin());
        (*slot)->calls.push_back(std::move(call));
    }
    if (g_catcherOrigPtrs[N]) g_catcherOrigPtrs[N](self);
}

typedef void(*CatcherHookFn)(Il2CppObject*);
static CatcherHookFn g_catcherHookFns[MAX_CATCHERS] = {
        catcherHook<0>,catcherHook<1>,catcherHook<2>,catcherHook<3>,catcherHook<4>,
        catcherHook<5>,catcherHook<6>,catcherHook<7>,catcherHook<8>,catcherHook<9>,
        catcherHook<10>,catcherHook<11>,catcherHook<12>,catcherHook<13>,catcherHook<14>,
        catcherHook<15>,catcherHook<16>,catcherHook<17>,catcherHook<18>,catcherHook<19>,
        catcherHook<20>,catcherHook<21>,catcherHook<22>,catcherHook<23>,catcherHook<24>,
        catcherHook<25>,catcherHook<26>,catcherHook<27>,catcherHook<28>,catcherHook<29>,
        catcherHook<30>,catcherHook<31>,catcherHook<32>,catcherHook<33>,catcherHook<34>,
        catcherHook<35>,catcherHook<36>,catcherHook<37>,catcherHook<38>,catcherHook<39>,
        catcherHook<40>,catcherHook<41>,catcherHook<42>,catcherHook<43>,catcherHook<44>,
        catcherHook<45>,catcherHook<46>,catcherHook<47>,catcherHook<48>,catcherHook<49>,
        catcherHook<50>,catcherHook<51>,catcherHook<52>,catcherHook<53>,catcherHook<54>,
        catcherHook<55>,catcherHook<56>,catcherHook<57>,catcherHook<58>,catcherHook<59>,
        catcherHook<60>,catcherHook<61>,catcherHook<62>,catcherHook<63>
};

// GameManager by the object itself (131b19): an edit that only names an address (scene
// update/destroy, addcomponent, invoke, loop add) carries no class name, so the request
// text check alone let it through. Resolve the address and look at its class chain.
static bool objectIsGameManager(const std::string& addrHex) {
    if (addrHex.empty()) return false;
    uintptr_t addr = (uintptr_t)strtoull(addrHex.c_str(), nullptr, 16);
    if (!addr) return false;
    Il2CppObject* obj = resolveComponentByAddr(addr);
    if (!obj || !obj->klass) return false;
    for (Class cur(obj->klass); cur.IsValid() && cur.GetClass(); cur = cur.GetParent()) {
        std::string low;
        for (const char* p = cur.GetClass()->name; p && *p; ++p) low.push_back((char)tolower((unsigned char)*p));
        if (low.find("gamemanager") != std::string::npos) return true;
    }
    return false;
}

static void startServer() {
    httplib::Server svr;
    // Any explorer write whose request mentions GameManager is refused (scene/instance
    // update and delete); reads and other writes work as before.
    svr.set_pre_routing_handler([](const httplib::Request& req, httplib::Response& res) {
        if (req.method != "GET") {
            std::string low;
            low.reserve(req.body.size());
            for (char c : req.body) low.push_back((char)tolower((unsigned char)c));
            if (low.find("gamemanager") != std::string::npos || objectIsGameManager(jsonVal(req.body, "addr"))) {
                res.status = 403;
                res.set_content("{\"ok\":false,\"error\":\"GameManager is locked\"}", "application/json");
                return httplib::Server::HandlerResponse::Handled;
            }
        }
        return httplib::Server::HandlerResponse::Unhandled;
    });

    svr.Get("/",[](const httplib::Request&, httplib::Response& res) {
        int n = 0;
        for (auto& img : Image::GetImages()) if (img.IsValid()) n++;
        res.set_content(GetExplorerHTML(n), "text/html");
    });

    svr.Get("/api/assemblies",[](const httplib::Request&, httplib::Response& res) {
        AttachThread();
        res.set_content(assembliesJson(), "application/json");
    });

    svr.Get("/api/classes",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        res.set_content(classesJson(req.get_param_value("a")), "application/json");
    });

    svr.Get("/api/allclasses",[](const httplib::Request&, httplib::Response& res) {
        AttachThread();
        res.set_content(allClassesJson(), "application/json");
    });

    svr.Get("/api/class",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        res.set_content(classDetailJson(req.get_param_value("a"), req.get_param_value("ns"), req.get_param_value("n")), "application/json");
    });

    svr.Get("/api/instances",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        res.set_content(instancesJson(req.get_param_value("a"), req.get_param_value("ns"), req.get_param_value("n")), "application/json");
    });

    svr.Get("/api/scene",[](const httplib::Request&, httplib::Response& res) {
        AttachThread();
        res.set_content(sceneJson(), "application/json");
    });

    svr.Get("/api/scene/inspect",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        auto s = req.get_param_value("addr");
        res.set_content(s.empty() ? "{}" : goInfoJson((uintptr_t)strtoull(s.c_str(), nullptr, 16)), "application/json");
    });

    svr.Get("/api/controller/inspect",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        uintptr_t addr = (uintptr_t)strtoull(req.get_param_value("addr").c_str(), nullptr, 16);
        std::string a = req.get_param_value("asm"), ns = req.get_param_value("ns"), cn = req.get_param_value("cls");

        Class startCls(ns.c_str(), cn.c_str(), Image(a.c_str()));
        if (!startCls.IsValid()) { res.set_content("{}", "application/json"); return; }

        std::ostringstream j;
        j << "{\"addr\":\"" << std::hex << addr << "\",\"name\":\"" << jsEsc(cn) << "\",\"fields\":[";

        bool firstF = true;
        std::vector<std::string> seen;

        for (Class cur = startCls; cur.IsValid() && cur.GetClass(); cur = cur.GetParent()) {
            auto* cn_ = cur.GetClass()->name;
            if (!cn_) break;
            if (strcmp(cn_, "Object") == 0 && cur.GetClass()->namespaze && strcmp(cur.GetClass()->namespaze, "System") == 0) break;

            for (auto& f : cur.GetFields(false)) {
                try {
                    auto* fi = f.GetInfo();
                    if (!fi || !fi->type) continue;
                    bool fStatic = (fi->type->attrs & 0x10) != 0;
                    if (!fStatic && !addr) continue;
                    std::string fn = fi->name;
                    bool dup = false; for (auto& s : seen) if (s==fn) dup=true;
                    if (dup) continue; seen.push_back(fn);
                    std::string ft = typeName(fi->type);
                    bool ok = false;
                    std::string vs = readField(ft, cur, fStatic ? nullptr : (Il2CppObject*)addr, fn, nullptr, fStatic, ok);
                    if (ok) { if (!firstF) j << ","; firstF = false; j << "{\"name\":\"" << jsEsc(fn) << "\",\"type\":\"" << ft << "\",\"val\":" << vs << ",\"isProp\":false,\"canWrite\":true,\"static\":" << (fStatic?"true":"false") << "}"; }
                } catch(...) {}
            }

            for (auto& p : cur.GetProperties(false)) {
                try {
                    auto* pi = p._data;
                    if (!pi || !pi->get) continue;
                    bool pStatic = (pi->get->flags & 0x10) != 0;
                    if (!pStatic && !addr) continue;
                    std::string pn = pi->name;
                    bool dup = false; for (auto& s : seen) if (s==pn) dup=true;
                    if (dup) continue; seen.push_back(pn);
                    std::string pt = typeName(pi->get->return_type);
                    bool ok = false;
                    MethodBase getter(pi->get);
                    std::string vs = readField(pt, cur, pStatic ? nullptr : (Il2CppObject*)addr, pn, &getter, pStatic, ok);
                    if (ok) { if (!firstF) j << ","; firstF = false; j << "{\"name\":\"" << jsEsc(pn) << "\",\"type\":\"" << pt << "\",\"val\":" << vs << ",\"isProp\":true,\"canWrite\":" << (pi->set?"true":"false") << ",\"static\":" << (pStatic?"true":"false") << "}"; }
                } catch(...) {}
            }
        }

        j << "],\"methods\":[";
        bool firstM = true;
        for (Class cur = startCls; cur.IsValid() && cur.GetClass(); cur = cur.GetParent()) {
            auto* cn_ = cur.GetClass()->name;
            if (!cn_) break;
            if (strcmp(cn_, "Object") == 0 && cur.GetClass()->namespaze && strcmp(cur.GetClass()->namespaze, "System") == 0) break;
            for (auto& m : cur.GetMethods(false)) {
                auto* mi = m.GetInfo();
                if (!m.IsValid() || !mi || !mi->name) continue;
                if (!firstM) j << ","; firstM = false;
                j << "{\"name\":\"" << jsEsc(mi->name) << "\",\"ret\":\"" << jsEsc(typeName(mi->return_type)) << "\",\"s\":" << ((mi->flags&0x10)?"true":"false") << ",\"params\":[";
                for (int i = 0; i < (int)mi->parameters_count; i++) {
                    if (i>0) j<<",";
                    j << "{\"n\":\"arg" << i << "\",\"t\":\"" << jsEsc(mi->parameters&&mi->parameters[i]?typeName(mi->parameters[i]):"?") << "\"}";
                }
                j << "]}";
            }
        }
        j << "]}";
        res.set_content(j.str(), "application/json");
    });

    svr.Post("/api/scene/update",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        uintptr_t addr = (uintptr_t)strtoull(jsonVal(req.body, "addr").c_str(), nullptr, 16);
        if (!addr) { res.set_content("{\"ok\":false}", "application/json"); return; }
        std::string type = jsonVal(req.body, "type"), prop = jsonVal(req.body, "prop"), val = jsonVal(req.body, "val");
        try {
            if (type == "gameobject") {
                if (prop == "active") {
                    bool newActive = val == "true";
                    Il2CppObject* go = resolveComponentByAddr(addr);
                    if (go) {
                        Method<void> setActive(Class("UnityEngine","GameObject").GetMethod("SetActive", 1));
                        if (setActive.IsValid()) setActive[go](newActive);
                    }
                } else if (prop == "name") {
                    Il2CppObject* go = resolveComponentByAddr(addr);
                    if (go) {
                        Method<void> m(Class("UnityEngine","Object").GetMethod("set_name",1));
                        if(m.IsValid()) m[go](CreateMonoString(val));
                    }
                }
            } else if (type == "transform") {
                float x=0,y=0,z=0; sscanf(val.c_str(),"[%f,%f,%f]",&x,&y,&z); Vector3 v={x,y,z};
                Il2CppObject* tr = resolveComponentByAddr(addr);
                if (tr) {
                    if (prop=="p") { Method<void> m(Class("UnityEngine","Transform").GetMethod("set_localPosition",1)); if(m.IsValid()) m[tr](v); }
                    else if (prop=="r") { Method<void> m(Class("UnityEngine","Transform").GetMethod("set_localEulerAngles",1)); if(m.IsValid()) m[tr](v); }
                    else if (prop=="s") { Method<void> m(Class("UnityEngine","Transform").GetMethod("set_localScale",1)); if(m.IsValid()) m[tr](v); }
                }
            } else if (type == "script") {
                std::string name = jsonVal(req.body, "prop2");
                std::string ft   = jsonVal(req.body, "ftype");
                bool isProp      = jsonVal(req.body, "isProp") == "true";
                if (prop == "enabled") {
                    try {
                        bool newEnabled = val == "true";
                        Il2CppObject* comp = resolveComponentByAddr(addr);
                        if (comp && comp->klass) {
                            auto setEnabledOnComp = [&](Il2CppObject* c, bool v) {
                                Class compKlass(c->klass);
                                for (Class cur = compKlass; cur.IsValid() && cur.GetClass(); cur = cur.GetParent()) {
                                    auto* cn_ = cur.GetClass()->name;
                                    if (!cn_ || strcmp(cn_, "Component") == 0 || strcmp(cn_, "Object") == 0) break;
                                    auto ep = cur.GetProperty("enabled");
                                    if (ep.IsValid() && ep._data && ep._data->set) {
                                        Method<void> m(ep._data->set); m.SetInstance(c); m(v); return true;
                                    }
                                }
                                return false;
                            };
                            auto getEnabledFromComp = [&](Il2CppObject* c) -> bool {
                                Class compKlass(c->klass);
                                for (Class cur = compKlass; cur.IsValid() && cur.GetClass(); cur = cur.GetParent()) {
                                    auto* cn_ = cur.GetClass()->name;
                                    if (!cn_ || strcmp(cn_, "Component") == 0 || strcmp(cn_, "Object") == 0) break;
                                    auto ep = cur.GetProperty("enabled");
                                    if (ep.IsValid() && ep._data && ep._data->get) {
                                        Method<bool> m(ep._data->get); m.SetInstance(c);
                                        try { return m(); } catch(...) {}
                                    }
                                }
                                return false;
                            };
                            if (newEnabled) {
                                bool already = getEnabledFromComp(comp);
                                if (!already) {
                                    setEnabledOnComp(comp, true);
                                    std::this_thread::sleep_for(std::chrono::milliseconds(50));
                                    comp = resolveComponentByAddr(addr);
                                    if (!comp) { res.set_content("{\"ok\":true,\"warn\":\"enabled but object moved\"}", "application/json"); return; }
                                }
                            } else {
                                setEnabledOnComp(comp, false);
                            }
                        }
                    } catch(...) {}
                } else {
                    std::string compNs  = jsonVal(req.body, "compNs");
                    std::string compCls = jsonVal(req.body, "compCls");
                    std::string compAsm = jsonVal(req.body, "compAsm");
                    Il2CppObject* obj = resolveComponentByAddr(addr);
                    if (!obj) { res.set_content("{\"ok\":false,\"error\":\"stale address\"}", "application/json"); return; }
                    Class startCls = (obj->klass) ? Class(obj->klass) :
                                     Class(compNs.c_str(), compCls.c_str(), compAsm.empty() ? Image() : Image(compAsm.c_str()));
                    for (Class cur = startCls; cur.IsValid(); cur = cur.GetParent()) {
                        if (isProp) {
                            auto p = cur.GetProperty(name.c_str());
                            if (p.IsValid() && p._data && p._data->set) { MethodBase s(p._data->set); writeField(ft, cur, obj, name, &s, val); break; }
                        } else {
                            auto f = cur.GetField(name.c_str());
                            if (f.IsValid()) { writeField(ft, cur, obj, name, nullptr, val); break; }
                        }
                    }
                }
            }
        } catch(...) {}
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Post("/api/instance/update",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        uintptr_t addr = (uintptr_t)strtoull(jsonVal(req.body, "addr").c_str(), nullptr, 16);
        std::string a=jsonVal(req.body,"asm"), ns=jsonVal(req.body,"ns"), cn=jsonVal(req.body,"cls");
        std::string name=jsonVal(req.body,"name"), ft=jsonVal(req.body,"ftype"), val=jsonVal(req.body,"val");
        bool isProp = jsonVal(req.body,"isProp") == "true";
        bool isStaticField = jsonVal(req.body,"isStatic") == "true";
        Il2CppObject* obj = isStaticField ? nullptr : resolveComponentByAddr(addr);
        if (obj || isStaticField) {
            try {
                for (Class cur(ns.c_str(),cn.c_str(),Image(a.c_str())); cur.IsValid(); cur = cur.GetParent()) {
                    if (isProp) {
                        auto p = cur.GetProperty(name.c_str());
                        if (p.IsValid() && p._data && p._data->set) { MethodBase s(p._data->set); writeField(ft,cur,obj,name,&s,val,isStaticField); break; }
                    } else {
                        auto f = cur.GetField(name.c_str());
                        if (f.IsValid()) { writeField(ft,cur,obj,name,nullptr,val,isStaticField); break; }
                    }
                }
            } catch(...) {}
        }
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Post("/api/scene/delete",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        auto s = jsonVal(req.body, "addr");
        if (!s.empty()) {
            uintptr_t addr = (uintptr_t)strtoull(s.c_str(), nullptr, 16);
            Il2CppObject* obj = resolveComponentByAddr(addr);
            if (obj) {
                try { Method<void> m(Class("UnityEngine","Object").GetMethod("Destroy",1)); if(m.IsValid()) m(obj); } catch(...) {}
            }
        }
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Post("/api/invoke",[](const httplib::Request& req, httplib::Response& res) {
        auto r = invokeMethod(req.body);
        std::ostringstream j;
        j << "{\"ok\":" << (r.ok?"true":"false") << ",\"value\":\"" << jsEsc(r.val) << "\",\"error\":\"" << jsEsc(r.err) << "\"}";
        res.set_content(j.str(), "application/json");
    });

    svr.Post("/api/loop/add",[](const httplib::Request& req, httplib::Response& res) {
        auto e = std::make_shared<LoopEntry>();
        e->addr      = jsonVal(req.body, "addr");
        e->asm_      = jsonVal(req.body, "asm");
        e->ns        = jsonVal(req.body, "ns");
        e->cls       = jsonVal(req.body, "cls");
        e->name      = jsonVal(req.body, "name");
        e->ftype     = jsonVal(req.body, "ftype");
        e->isProp    = jsonVal(req.body, "isProp") == "true";
        e->val       = jsonVal(req.body, "val");
        int ms       = 0;
        try { ms = std::stoi(jsonVal(req.body, "interval")); } catch(...) {}
        if (ms < 50) ms = 100;
        e->intervalMs = ms;

        std::string id = e->addr + "_" + e->cls + "_" + e->name;
        {
            std::lock_guard<std::mutex> lk(g_loopMtx);
            auto it = g_loops.find(id);
            if (it != g_loops.end()) it->second->active = false;
            g_loops[id] = e;
        }

        std::thread([e, id]() {
            AttachThread();
            while (e->active) {
                applyLoopEntry(e);
                std::this_thread::sleep_for(std::chrono::milliseconds(e->intervalMs));
            }
            std::lock_guard<std::mutex> lk(g_loopMtx);
            auto it = g_loops.find(id);
            if (it != g_loops.end() && it->second == e) g_loops.erase(it);
        }).detach();

        std::ostringstream j;
        j << "{\"ok\":true,\"id\":\"" << jsEsc(id) << "\"}";
        res.set_content(j.str(), "application/json");
    });

    svr.Post("/api/loop/remove",[](const httplib::Request& req, httplib::Response& res) {
        std::string id = jsonVal(req.body, "id");
        std::lock_guard<std::mutex> lk(g_loopMtx);
        auto it = g_loops.find(id);
        if (it != g_loops.end()) { it->second->active = false; g_loops.erase(it); }
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Post("/api/loop/removeall",[](const httplib::Request&, httplib::Response& res) {
        std::lock_guard<std::mutex> lk(g_loopMtx);
        for (auto& kv : g_loops) kv.second->active = false;
        g_loops.clear();
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Get("/api/loop/list",[](const httplib::Request&, httplib::Response& res) {
        res.set_content(loopListJson(), "application/json");
    });

    svr.Get("/api/logs",[](const httplib::Request&, httplib::Response& res) {
        res.set_content(logsJson(), "application/json");
    });

    svr.Post("/api/logs/clear",[](const httplib::Request&, httplib::Response& res) {
        std::lock_guard<std::mutex> lk(g_logMtx);
        g_logBuffer.clear();
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Post("/api/logs/add",[](const httplib::Request& req, httplib::Response& res) {
        std::string msg = jsonVal(req.body, "msg");
        int level = 0;
        try { level = std::stoi(jsonVal(req.body, "level")); } catch(...) {}
        addLogEntry(msg, level);
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Get("/api/timescale",[](const httplib::Request&, httplib::Response& res) {
        AttachThread();
        float ts = 1.0f;
        try {
            Class timeCls("UnityEngine", "Time");
            if (timeCls.IsValid()) {
                Method<float> get(timeCls.GetMethod("get_timeScale"));
                if (get.IsValid()) ts = get();
            }
        } catch(...) {}
        res.set_content("{\"value\":" + std::to_string(ts) + "}", "application/json");
    });

    svr.Post("/api/timescale",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        float val = 1.0f;
        try { val = std::stof(jsonVal(req.body, "value")); } catch(...) {}
        try {
            Class timeCls("UnityEngine", "Time");
            if (timeCls.IsValid()) {
                Method<void> set(timeCls.GetMethod("set_timeScale", 1));
                if (set.IsValid()) set(val);
            }
        } catch(...) {}
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Post("/api/scene/destroy",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        auto s = jsonVal(req.body, "addr");
        if (!s.empty()) {
            uintptr_t addr = (uintptr_t)strtoull(s.c_str(), nullptr, 16);
            Il2CppObject* obj = resolveComponentByAddr(addr);
            if (obj) {
                try {
                    Method<void> m(Class("UnityEngine","Object").GetMethod("Destroy",1));
                    if (m.IsValid()) m(obj);
                } catch(...) {}
            }
        }
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Post("/api/scene/create",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        std::string name = jsonVal(req.body, "name");
        if (name.empty()) name = "New GameObject";
        std::ostringstream j;
        try {
            Class goCls("UnityEngine", "GameObject");
            Method<Il2CppObject*> ctor;
            for (auto& m : goCls.GetMethods(false)) {
                auto* mi = m.GetInfo();
                if (mi && mi->name && strcmp(mi->name, ".ctor") == 0 && mi->parameters_count == 1) {
                    ctor = Method<Il2CppObject*>(m);
                    break;
                }
            }
            if (ctor.IsValid()) {
                Il2CppObject* newGO = goCls.CreateNewObjectParameters(CreateMonoString(name.c_str()));
                if (newGO) {
                    j << "{\"ok\":true,\"addr\":\"" << std::hex << (uintptr_t)newGO << "\"}";
                    res.set_content(j.str(), "application/json");
                    return;
                }
            }
        } catch(...) {}
        res.set_content("{\"ok\":false,\"error\":\"Failed to create GameObject\"}", "application/json");
    });

    svr.Post("/api/scene/addcomponent",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        uintptr_t addr = (uintptr_t)strtoull(jsonVal(req.body, "addr").c_str(), nullptr, 16);
        std::string typeName_ = jsonVal(req.body, "type");
        if (!addr || typeName_.empty()) { res.set_content("{\"ok\":false}", "application/json"); return; }
        Il2CppObject* go = resolveComponentByAddr(addr);
        if (!go) { res.set_content("{\"ok\":false,\"error\":\"stale address\"}", "application/json"); return; }
        try {
            Il2CppObject* typeObj = sysTypeOf(typeName_);
            if (!typeObj) typeObj = sysTypeOf(typeName_ + ", UnityEngine.CoreModule");
            if (!typeObj) typeObj = sysTypeOf(typeName_ + ", UnityEngine");
            if (!typeObj) typeObj = sysTypeOf(typeName_ + ", Assembly-CSharp");
            if (typeObj) {
                Class goCls("UnityEngine", "GameObject");
                Method<Il2CppObject*> addComp(goCls.GetMethod("AddComponent", 1));
                if (addComp.IsValid()) {
                    Il2CppObject* comp = addComp[go](typeObj);
                    if (comp) {
                        std::ostringstream j;
                        j << "{\"ok\":true,\"addr\":\"" << std::hex << (uintptr_t)comp << "\"}";
                        res.set_content(j.str(), "application/json");
                        return;
                    }
                }
            }
        } catch(...) {}
        res.set_content("{\"ok\":false,\"error\":\"Failed to add component\"}", "application/json");
    });

    svr.Post("/api/scene/removecomponent",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        uintptr_t addr = (uintptr_t)strtoull(jsonVal(req.body, "addr").c_str(), nullptr, 16);
        if (!addr) { res.set_content("{\"ok\":false}", "application/json"); return; }
        Il2CppObject* obj = resolveComponentByAddr(addr);
        if (!obj) { res.set_content("{\"ok\":false,\"error\":\"stale address\"}", "application/json"); return; }
        try {
            Method<void> destroy(Class("UnityEngine","Object").GetMethod("Destroy",1));
            if (destroy.IsValid()) destroy(obj);
        } catch(...) {}
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Get("/api/dump",[](const httplib::Request&, httplib::Response& res) {
        AttachThread();
        std::ostringstream out;
        for (auto& img : Image::GetImages()) {
            auto* d = img.GetInfo();
            if (!img.IsValid() || !d || !d->name) continue;
            out << "// Assembly: " << d->name << "\n\n";
            for (auto& cls : img.GetClasses(false)) {
                auto* r = cls.GetClass();
                if (!cls.IsValid() || !r || !r->name) continue;
                std::string clsKw = "class";
                if (r->enumtype) clsKw = "enum";
                else if (r->byval_arg.valuetype) clsKw = "struct";
                else if (r->flags & 0x20) clsKw = "interface";
                if (r->namespaze && strlen(r->namespaze) > 0) {
                    out << "namespace " << r->namespaze << " {\n";
                }
                std::string indent = (r->namespaze && strlen(r->namespaze) > 0) ? "    " : "";
                std::string parent = "";
                if (r->parent && r->parent->name && strcmp(r->parent->name, "Object") != 0 && strcmp(r->parent->name, "ValueType") != 0)
                    parent = " : " + std::string(r->parent->namespaze && strlen(r->parent->namespaze) > 0 ? std::string(r->parent->namespaze) + "." : "") + r->parent->name;
                out << indent << "public " << clsKw << " " << r->name << parent << " {\n";
                for (auto& f : cls.GetFields(false)) {
                    auto* fi = f.GetInfo();
                    if (!fi || !fi->name) continue;
                    std::string tn = typeName(fi->type);
                    bool isStatic = fi->type && (fi->type->attrs & 0x10);
                    out << indent << "    // offset 0x" << std::hex << fi->offset << std::dec << "\n";
                    out << indent << "    public " << (isStatic ? "static " : "") << tn << " " << fi->name << ";\n";
                }
                for (auto& p : cls.GetProperties(false)) {
                    auto* pi = p._data;
                    if (!pi || !pi->name) continue;
                    std::string tn = pi->get && pi->get->return_type ? typeName(pi->get->return_type) : "?";
                    out << indent << "    public " << tn << " " << pi->name << " {";
                    if (pi->get) out << " get;";
                    if (pi->set) out << " set;";
                    out << " }\n";
                }
                for (auto& m : cls.GetMethods(false)) {
                    auto* mi = m.GetInfo();
                    if (!mi || !mi->name) continue;
                    char ab[32] = {};
                    if (mi->methodPointer) snprintf(ab, sizeof(ab), "0x%llX", (unsigned long long)(uintptr_t)mi->methodPointer);
                    bool isStatic = mi->flags & 0x10;
                    out << indent << "    // " << ab << "\n";
                    out << indent << "    public " << (isStatic ? "static " : "") << typeName(mi->return_type) << " " << mi->name << "(";
                    for (int i = 0; i < (int)mi->parameters_count; i++) {
                        if (i > 0) out << ", ";
                        out << (mi->parameters && mi->parameters[i] ? typeName(mi->parameters[i]) : "?") << " arg" << i;
                    }
                    out << ") {}\n";
                }
                out << indent << "}\n";
                if (r->namespaze && strlen(r->namespaze) > 0) out << "}\n";
                out << "\n";
            }
        }
        res.set_content(out.str(), "text/plain");
    });

    svr.Get("/api/scene/addable",[](const httplib::Request&, httplib::Response& res) {
        AttachThread();
        std::ostringstream j;
        j << "{";
        bool firstCat = true;
        static const struct { const char* cat; const char* ns; const char* name; } known[] = {
                {"Physics","UnityEngine","Rigidbody"},{"Physics","UnityEngine","BoxCollider"},{"Physics","UnityEngine","SphereCollider"},
                {"Physics","UnityEngine","CapsuleCollider"},{"Physics","UnityEngine","MeshCollider"},{"Physics","UnityEngine","CharacterController"},
                {"Physics","UnityEngine","WheelCollider"},{"Physics","UnityEngine","Rigidbody2D"},{"Physics","UnityEngine","BoxCollider2D"},
                {"Physics","UnityEngine","CircleCollider2D"},{"Physics","UnityEngine","PolygonCollider2D"},{"Physics","UnityEngine","CapsuleCollider2D"},
                {"Rendering","UnityEngine","MeshRenderer"},{"Rendering","UnityEngine","MeshFilter"},{"Rendering","UnityEngine","SkinnedMeshRenderer"},
                {"Rendering","UnityEngine","SpriteRenderer"},{"Rendering","UnityEngine","LineRenderer"},{"Rendering","UnityEngine","TrailRenderer"},
                {"Rendering","UnityEngine","Camera"},{"Rendering","UnityEngine","Light"},
                {"Audio","UnityEngine","AudioSource"},{"Audio","UnityEngine","AudioListener"},{"Audio","UnityEngine","AudioReverbZone"},
                {"Animation","UnityEngine","Animator"},{"Animation","UnityEngine","Animation"},{"Animation","UnityEngine","ParticleSystem"},
                {"Navigation","UnityEngine","NavMeshAgent"},{"Navigation","UnityEngine","NavMeshObstacle"},
                {"UI","UnityEngine.UI","Text"},{"UI","UnityEngine.UI","Image"},{"UI","UnityEngine.UI","Button"},
                {"UI","UnityEngine.UI","Toggle"},{"UI","UnityEngine.UI","Slider"},{"UI","UnityEngine.UI","InputField"},
                {"UI","UnityEngine.UI","Canvas"},{"UI","UnityEngine","CanvasGroup"},
                {nullptr,nullptr,nullptr}
        };
        std::map<std::string, std::vector<std::pair<std::string,std::string>>> cats;
        for (int i = 0; known[i].cat; i++) {
            Class c(known[i].ns, known[i].name);
            if (c.IsValid()) cats[known[i].cat].push_back({std::string(known[i].ns) + "." + known[i].name, known[i].name});
        }
        for (auto& img : Image::GetImages()) {
            auto* d = img.GetInfo();
            if (!img.IsValid() || !d || !d->name) continue;
            if (strstr(d->name, "Assembly-CSharp") == nullptr && strstr(d->name, "Assembly-CSharp-firstpass") == nullptr) continue;
            for (auto& cls : img.GetClasses(false)) {
                auto* r = cls.GetClass();
                if (!cls.IsValid() || !r || !r->name) continue;
                bool isMono = false;
                for (auto* cur = r; cur; cur = cur->parent)
                    if (cur->name && strcmp(cur->name, "MonoBehaviour") == 0) { isMono = true; break; }
                if (!isMono) continue;
                std::string ns = r->namespaze ? r->namespaze : "";
                std::string fullName = ns.empty() ? r->name : ns + "." + r->name;
                cats["Scripts"].push_back({fullName, r->name});
            }
        }
        for (auto& kv : cats) {
            if (!firstCat) j << ","; firstCat = false;
            j << "\"" << jsEsc(kv.first) << "\":[";
            bool first = true;
            for (auto& p : kv.second) {
                if (!first) j << ","; first = false;
                j << "{\"full\":\"" << jsEsc(p.first) << "\",\"name\":\"" << jsEsc(p.second) << "\"}";
            }
            j << "]";
        }
        j << "}";
        res.set_content(j.str(), "application/json");
    });

    svr.Post("/api/catcher/hook",[](const httplib::Request& req, httplib::Response& res) {
        AttachThread();
        std::string asm_ = jsonVal(req.body, "asm");
        std::string ns   = jsonVal(req.body, "ns");
        std::string cls  = jsonVal(req.body, "cls");
        std::string meth = jsonVal(req.body, "method");
        std::string id   = asm_ + "|" + ns + "|" + cls + "|" + meth;
        {
            std::lock_guard<std::mutex> lk(g_catcherMtx);
            if (g_catchers.count(id)) {
                res.set_content(std::string("{\"ok\":true,\"exists\":true,\"id\":\"") + jsEsc(id) + "\"}", "application/json");
                return;
            }
        }
        int slot = -1;
        {
            std::lock_guard<std::mutex> lk(g_catcherSlotMtx);
            for (int i = 0; i < MAX_CATCHERS; i++) {
                if (!g_catcherSlots[i]) { slot = i; break; }
            }
        }
        if (slot < 0) { res.set_content("{\"ok\":false,\"error\":\"max catchers reached\"}", "application/json"); return; }
        auto entry = std::make_shared<CatcherEntry>();
        entry->id = id; entry->asm_ = asm_; entry->ns = ns; entry->cls = cls; entry->method = meth;
        entry->slotIdx = slot;
        Class c(ns.c_str(), cls.c_str(), Image(asm_.c_str()));
        if (!c.IsValid()) { res.set_content("{\"ok\":false,\"error\":\"class not found\"}", "application/json"); return; }
        MethodBase mb = c.GetMethod(meth.c_str());
        if (!mb.IsValid()) { res.set_content("{\"ok\":false,\"error\":\"method not found\"}", "application/json"); return; }
        auto* mi = mb.GetInfo();
        if (!mi || !mi->methodPointer) { res.set_content("{\"ok\":false,\"error\":\"no method pointer\"}", "application/json"); return; }
        entry->retType = typeName(mi->return_type);
        for (int i = 0; i < (int)mi->parameters_count; i++) {
            entry->paramTypes.push_back(mi->parameters && mi->parameters[i] ? typeName(mi->parameters[i]) : "?");
            entry->paramNames.push_back("arg" + std::to_string(i));
        }
        {
            std::lock_guard<std::mutex> lk(g_catcherSlotMtx);
            g_catcherSlots[slot] = new std::shared_ptr<CatcherEntry>(entry);
        }
        InvokeHook(mb, g_catcherHookFns[slot], g_catcherOrigPtrs[slot]);
        {
            std::lock_guard<std::mutex> lk(g_catcherMtx);
            g_catchers[id] = entry;
        }
        std::ostringstream j;
        j << "{\"ok\":true,\"id\":\"" << jsEsc(id) << "\"}";
        res.set_content(j.str(), "application/json");
    });

    svr.Post("/api/catcher/unhook",[](const httplib::Request& req, httplib::Response& res) {
        std::string id = jsonVal(req.body, "id");
        std::lock_guard<std::mutex> lk(g_catcherMtx);
        auto it = g_catchers.find(id);
        if (it != g_catchers.end()) {
            it->second->active = false;
            int sl = it->second->slotIdx;
            if (sl >= 0 && sl < MAX_CATCHERS) {
                std::lock_guard<std::mutex> lk2(g_catcherSlotMtx);
                delete g_catcherSlots[sl];
                g_catcherSlots[sl] = nullptr;
                g_catcherOrigPtrs[sl] = nullptr;
            }
            g_catchers.erase(it);
        }
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Post("/api/catcher/clear",[](const httplib::Request& req, httplib::Response& res) {
        std::string id = jsonVal(req.body, "id");
        std::lock_guard<std::mutex> lk(g_catcherMtx);
        auto it = g_catchers.find(id);
        if (it != g_catchers.end()) { std::lock_guard<std::mutex> lk2(it->second->callsMtx); it->second->calls.clear(); }
        res.set_content("{\"ok\":true}", "application/json");
    });

    svr.Get("/api/catcher/list",[](const httplib::Request&, httplib::Response& res) {
        std::lock_guard<std::mutex> lk(g_catcherMtx);
        std::ostringstream j;
        j << "[";
        bool first = true;
        for (auto& kv : g_catchers) {
            auto& e = kv.second;
            if (!first) j << ","; first = false;
            j << "{\"id\":\"" << jsEsc(e->id) << "\",\"cls\":\"" << jsEsc(e->cls) << "\",\"ns\":\"" << jsEsc(e->ns) << "\",\"method\":\"" << jsEsc(e->method) << "\",\"ret\":\"" << jsEsc(e->retType) << "\",\"active\":" << (e->active?"true":"false") << ",\"count\":";
            { std::lock_guard<std::mutex> lk2(e->callsMtx); j << e->calls.size(); }
            j << ",\"params\":[";
            for (size_t i = 0; i < e->paramTypes.size(); i++) {
                if (i>0) j<<",";
                j << "{\"t\":\"" << jsEsc(e->paramTypes[i]) << "\",\"n\":\"" << jsEsc(e->paramNames[i]) << "\"}";
            }
            j << "]}";
        }
        j << "]";
        res.set_content(j.str(), "application/json");
    });

    svr.Get("/api/catcher/calls",[](const httplib::Request& req, httplib::Response& res) {
        std::string id = req.get_param_value("id");
        std::lock_guard<std::mutex> lk(g_catcherMtx);
        auto it = g_catchers.find(id);
        if (it == g_catchers.end()) { res.set_content("[]", "application/json"); return; }
        auto& e = it->second;
        std::lock_guard<std::mutex> lk2(e->callsMtx);
        std::ostringstream j;
        j << "[";
        bool first = true;
        for (auto& c : e->calls) {
            if (!first) j << ","; first = false;
            j << "{\"ts\":" << std::fixed << c.ts << ",\"instance\":\"" << jsEsc(c.instance) << "\",\"args\":[";
            for (size_t i = 0; i < c.args.size(); i++) {
                if (i>0) j<<",";
                j << "{\"t\":\"" << jsEsc(c.args[i].type) << "\",\"v\":" << c.args[i].val << "}";
            }
            j << "],\"ret\":\"" << jsEsc(c.ret) << "\"}";
        }
        j << "]";
        res.set_content(j.str(), "application/json");
    });

    svr.Post("/api/lua/exec",[](const httplib::Request& req, httplib::Response& res) {
        try {
            std::string code = jsonVal(req.body, "code");
            if (code.empty()) { res.set_content("{\"output\":\"Error: empty code\"}", "application/json"); return; }
            std::string out = executeLuaBNM(code);
            std::ostringstream j;
            j << "{\"output\":\"" << jsEsc(out) << "\"}";
            res.set_content(j.str(), "application/json");
        } catch (...) {
            res.set_content("{\"output\":\"Error: native crash in executor\"}", "application/json");
        }
    });

    // Bind to localhost ONLY (not 0.0.0.0) so the Explorer is reachable only
    // on-device (adb forward tcp:... tcp:8080 still works for debugging) and
    // NOT by anyone else on the player's network — closing a live-game
    // inspect/edit hole in the public build.
    svr.listen("127.0.0.1", g_port);
}

// --- Entitlement flow observation hooks ---------------------------------
// Armed in-process from OnLoaded(), before any HTTP round trip is possible,
// so we don't lose the race against Unity's Awake()/Start() lifecycle (which
// fires within milliseconds of process resume - manually arming hooks over
// curl after the fact is consistently too slow to catch it).
//
// Uses BasicHook (Dobby inline hook on the real function address) rather
// than BNM's InvokeHook/MethodInfo-swap technique or the built-in "catcher"
// system: InvokeHook only catches calls that go through il2cpp API method
// dispatch (reflection/runtime_invoke), not direct compiled call sites,
// which is how normal C# method calls are actually emitted. The built-in
// catcher only forwards `self` (no other args), which would corrupt any
// call to a method that takes real parameters - unsafe for these targets.
//
// Native IL2CPP ABI (see BNM::Method::Call in Method.hpp): non-static
// methods are `Ret(*)(Il2CppObject*, Params..., MethodInfo*)`; static
// methods (UNITY_VER > 174, true here) are `Ret(*)(Params..., MethodInfo*)`.
// Reference-type params (objects, delegates, List<T>) are all pointer-sized
// so treating unknown/obfuscated param types generically as void* is ABI-safe.


// =====================================================================
//  YEEPS 1.19.0 (Unity 2021.3.19f1) — Oculus Platform entitlement bypass
// ---------------------------------------------------------------------
//  Ground truth from on-device logcat (com.TrassGames.Yeeps 1.19.0):
//    * "Entitlement: Beginning entitlement check"
//    * "Entitlement: Received check callback"          <- ENTITLEMENT PASSES
//    * "Entitlement: Failed to get user age group"     <- 400 Invalid OAuth
//    * "Loading Screen: Getting user proof..."
//    * "Entitlement: Failed to get user proof: 400"    <- 400 Invalid OAuth  <== THE GATE
//  After the proof fails the account never goes online, NetworkManager
//  never connects to Photon, and the client stalls on the loading screen.
//
//  The proof / age / nonce results are delivered through the NATIVE Oculus
//  Platform SDK (libovrplatformloader.so) and read by the managed C# entitlement
//  callbacks via P/Invoke of plain C exports. Those exports are name-stable
//  across builds, so we hook them directly with Dobby — no IL2CPP metadata /
//  obfuscated-name resolution needed for the core fix.
//
//  ovrMessageType constants (from the 1.19.0 il2cpp metadata dump,
//  Oculus.Platform.MessageType enum — the member NAMES survive obfuscation):
//     User_GetUserProof            = 578880643
//     UserAgeCategory_Get          = 567009472
//     User_GetLoggedInUser         = 1131361373
//
//  For an errored proof/age message we force ovr_Message_IsError -> false and
//  hand the managed callback a fabricated (non-null) handle whose getter returns
//  a dummy value, so the callback takes its SUCCESS branch. The dummy nonce is
//  then sent to the Yeeps backend (redirected to the self-hosted server via the
//  metadata URL patch), which does not validate it against Meta.
// =====================================================================

#include "extern/Dobby/include/dobby.h"

static const int OVRMSG_User_GetUserProof    = 578880643;
static const int OVRMSG_UserAgeCategory_Get  = 567009472;
// Meta friends ("me/friends") always fails with HTTP 400 for this build, which
// made the game pop "Failed to get friends: 400" for everyone. Treat it as a
// successful, EMPTY friend list instead (temporary in-game friends still work).
static const int OVRMSG_User_GetLoggedInUserFriends = 1484532365;
static const int OVRMSG_User_GetNextUserArrayPage   = 645723971;
static char g_fakeUserArray[8] = {0};

typedef unsigned char ovrBool;
typedef int           (*ovr_GetType_t)(void* msg);
typedef ovrBool       (*ovr_IsError_t)(void* msg);
typedef void*         (*ovr_GetUserProof_t)(void* msg);
typedef const char*   (*ovr_GetNonce_t)(void* proof);
typedef void*         (*ovr_GetAgeMsg_t)(void* msg);
typedef int           (*ovr_GetAgeCat_t)(void* handle);

static ovr_GetType_t      real_GetType     = nullptr;   // called, never hooked
static ovr_IsError_t      real_IsError     = nullptr;
static ovr_GetUserProof_t real_GetUserProof= nullptr;
static ovr_GetNonce_t     real_GetNonce    = nullptr;
static ovr_GetAgeMsg_t    real_GetAgeMsg   = nullptr;
static ovr_GetAgeCat_t    real_GetAgeCat   = nullptr;

// Sentinel handles handed back for errored messages (must be non-null & unique).
static char        g_fakeProofHandle[8] = {0};
static char        g_fakeAgeHandle[8]   = {0};
// A plausible 64-hex-char nonce. The redirected backend accepts any value.
static const char* g_dummyNonce = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90";

static ovrBool hook_ovr_Message_IsError(void* msg) {
    ovrBool err = real_IsError ? real_IsError(msg) : (ovrBool)0;
    if (err && real_GetType) {
        int t = real_GetType(msg);
        if (t == OVRMSG_User_GetUserProof || t == OVRMSG_UserAgeCategory_Get ||
            t == OVRMSG_User_GetLoggedInUserFriends || t == OVRMSG_User_GetNextUserArrayPage) {
            BNM_FileCheckpointF("[ovr] IsError: forcing SUCCESS for msg type %d (real=error)", t);
            return (ovrBool)0;
        }
    }
    return err;
}

static void* hook_ovr_Message_GetUserProof(void* msg) {
    // If the underlying proof message really errored, its data pointer is
    // unsafe — return our sentinel so GetNonce yields the dummy nonce.
    if (real_IsError && real_IsError(msg)) {
        BNM_FileCheckpoint("[ovr] GetUserProof: errored msg -> sentinel handle");
        return (void*)g_fakeProofHandle;
    }
    void* p = real_GetUserProof ? real_GetUserProof(msg) : nullptr;
    return p ? p : (void*)g_fakeProofHandle;
}

static const char* hook_ovr_UserProof_GetNonce(void* proof) {
    if (proof == (void*)g_fakeProofHandle) {
        BNM_FileCheckpoint("[ovr] GetNonce: sentinel -> dummy nonce");
        return g_dummyNonce;
    }
    const char* n = real_GetNonce ? real_GetNonce(proof) : nullptr;
    return n ? n : g_dummyNonce;
}

static void* hook_ovr_Message_GetUserAccountAgeCategory(void* msg) {
    if (real_IsError && real_IsError(msg)) {
        BNM_FileCheckpoint("[ovr] GetAgeCategory msg: errored -> sentinel handle");
        return (void*)g_fakeAgeHandle;
    }
    void* h = real_GetAgeMsg ? real_GetAgeMsg(msg) : nullptr;
    return h ? h : (void*)g_fakeAgeHandle;
}

static int hook_ovr_UserAccountAgeCategory_GetAgeCategory(void* handle) {
    if (handle == (void*)g_fakeAgeHandle) {
        return 3; // Adult (enum: Unknown=0, Child=1, Teen=2, Adult=3)
    }
    return real_GetAgeCat ? real_GetAgeCat(handle) : 3;
}

typedef void*       (*ovr_GetUserArray_t)(void* msg);
typedef size_t      (*ovr_UA_GetSize_t)(void* arr);
typedef ovrBool     (*ovr_UA_HasNext_t)(void* arr);
typedef const char* (*ovr_UA_NextUrl_t)(void* arr);
static ovr_GetUserArray_t real_GetUserArray = nullptr;
static ovr_UA_GetSize_t   real_UA_GetSize   = nullptr;
static ovr_UA_HasNext_t   real_UA_HasNext   = nullptr;
static ovr_UA_NextUrl_t   real_UA_NextUrl   = nullptr;
static void* hook_ovr_Message_GetUserArray(void* msg) {
    if (real_IsError && real_IsError(msg)) return (void*)g_fakeUserArray;   // errored -> empty list
    void* a = real_GetUserArray ? real_GetUserArray(msg) : nullptr;
    return a ? a : (void*)g_fakeUserArray;
}
static size_t hook_ovr_UserArray_GetSize(void* arr) {
    if (arr == (void*)g_fakeUserArray) return 0;
    return real_UA_GetSize ? real_UA_GetSize(arr) : 0;
}
static ovrBool hook_ovr_UserArray_HasNextPage(void* arr) {
    if (arr == (void*)g_fakeUserArray) return (ovrBool)0;
    return real_UA_HasNext ? real_UA_HasNext(arr) : (ovrBool)0;
}
static const char* hook_ovr_UserArray_GetNextUrl(void* arr) {
    if (arr == (void*)g_fakeUserArray) return "";
    return real_UA_NextUrl ? real_UA_NextUrl(arr) : "";
}

static bool g_ovrHooksInstalled = false;
static void InstallOvrPlatformHooks(void* lib) {
    if (g_ovrHooksInstalled) return;
    auto H = [&](const char* name, void* repl, void** orig) {
        void* sym = dlsym(lib, name);
        if (!sym) { BNM_FileCheckpointF("[ovr] dlsym FAILED: %s", name); return; }
        void* tramp = nullptr;
        if (DobbyHook(sym, (dobby_dummy_func_t)repl, (dobby_dummy_func_t*)&tramp) != 0) {
            BNM_FileCheckpointF("[ovr] DobbyHook FAILED: %s", name);
            return;
        }
        if (orig) *orig = tramp;
        BNM_FileCheckpointF("[ovr] hooked %s @ %p (tramp=%p)", name, sym, tramp);
    };
    // GetType is only called, not hooked.
    real_GetType = (ovr_GetType_t)dlsym(lib, "ovr_Message_GetType");
    BNM_FileCheckpointF("[ovr] ovr_Message_GetType @ %p", (void*)real_GetType);
    H("ovr_Message_IsError",                      (void*)hook_ovr_Message_IsError,                      (void**)&real_IsError);
    H("ovr_Message_GetUserProof",                 (void*)hook_ovr_Message_GetUserProof,                 (void**)&real_GetUserProof);
    H("ovr_UserProof_GetNonce",                   (void*)hook_ovr_UserProof_GetNonce,                   (void**)&real_GetNonce);
    H("ovr_Message_GetUserAccountAgeCategory",    (void*)hook_ovr_Message_GetUserAccountAgeCategory,    (void**)&real_GetAgeMsg);
    H("ovr_UserAccountAgeCategory_GetAgeCategory",(void*)hook_ovr_UserAccountAgeCategory_GetAgeCategory,(void**)&real_GetAgeCat);
    H("ovr_Message_GetUserArray",                 (void*)hook_ovr_Message_GetUserArray,                 (void**)&real_GetUserArray);
    H("ovr_UserArray_GetSize",                    (void*)hook_ovr_UserArray_GetSize,                    (void**)&real_UA_GetSize);
    H("ovr_UserArray_HasNextPage",                (void*)hook_ovr_UserArray_HasNextPage,                (void**)&real_UA_HasNext);
    H("ovr_UserArray_GetNextUrl",                 (void*)hook_ovr_UserArray_GetNextUrl,                 (void**)&real_UA_NextUrl);
    g_ovrHooksInstalled = true;
    BNM_FileCheckpoint("[ovr] InstallOvrPlatformHooks: DONE");
}

// libovrplatformloader.so is dlopen'd by the app during OVRPlatform init, which
// happens a little after process start. Poll (without forcing a load) until it
// appears, then install the hooks before the entitlement callbacks fire.
static void OvrHookPollThread() {
    // Poll effectively forever (process lifetime). The game may sit on the
    // Terms-of-Service / boot screens for an unbounded time before the game
    // scene loads and OVRPlatform (libovrplatformloader.so) is dlopen'd, so a
    // short timeout can miss it entirely on a fresh install. 100ms polling for
    // up to ~1 hour costs nothing and guarantees we catch the lib whenever it
    // loads, before the entitlement callbacks fire.
    for (int i = 0; i < 36000; i++) { // up to ~1 h
        void* lib = dlopen("libovrplatformloader.so", RTLD_NOLOAD | RTLD_NOW);
        if (lib) {
            BNM_FileCheckpointF("[ovr] libovrplatformloader.so present (attempt %d)", i);
            InstallOvrPlatformHooks(lib);
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    BNM_FileCheckpoint("[ovr] libovrplatformloader.so never appeared (1h timeout)");
}

// ---------------------------------------------------------------------
//  GameAnalytics kill-switch. The baked-in GameAnalytics SDK
//  (GameAnalyticsSDK.GameAnalytics, Assembly-CSharp.dll) opens a local
//  ga.sqlite3, starts a session and ships events to api.gameanalytics.com
//  (AWS us-east-1) -> player telemetry leaks to a GA account we don't own.
//  We no-op the single entry point GameAnalytics.Initialize(): with no
//  init there is no session, no DB and no network submission, and every
//  event method (NewDesignEvent/NewProgressionEvent/...) bails internally
//  on the un-initialized state, so gameplay is unaffected.
// ---------------------------------------------------------------------
static void (*orig_GA_Initialize)(IL2CPP::MethodInfo*) = nullptr;
static void hook_GA_Initialize([[maybe_unused]] IL2CPP::MethodInfo* mi) {
    static std::atomic<bool> logged{false};
    if (!logged.exchange(true))
        BNM_FileCheckpoint("GA: Initialize() suppressed - no telemetry");
    // intentionally do nothing: GA never starts
}
static void InstallGameAnalyticsBlock() {
    try {
        Class ga("GameAnalyticsSDK", "GameAnalytics", Image("Assembly-CSharp.dll"));
        if (!ga.IsValid()) { BNM_FileCheckpoint("GA: class NOT FOUND"); return; }
        MethodBase m = ga.GetMethod("Initialize", 0);
        if (m.IsValid()) { BasicHook(m, hook_GA_Initialize, orig_GA_Initialize);
                           BNM_FileCheckpoint("GA: Initialize() hooked (telemetry disabled)"); }
        else BNM_FileCheckpoint("GA: Initialize(0) NOT FOUND");
    } catch (...) { BNM_FileCheckpoint("GA: EXCEPTION installing block"); }
}

// ---------------------------------------------------------------------
//  Clean version label. ApplicationVersionDisplayUI.Start() builds the
//  on-screen string as  "v" + "{0}({1})"  (app version + a compatibility
//  version), e.g. "v1.19.0-blaze(1.19.0)". We let Start() run, then
//  overwrite versionText (field @ 0x18) with just Application.version
//  ("1.19.0-blaze") so the label reads exactly the manifest version.
// ---------------------------------------------------------------------
static void* (*p_gchandle_new)(void*, bool) = nullptr;   // il2cpp GC handles (Remote client)
static void (*orig_AVDU_Start)(void*, void*) = nullptr;
static void hook_AVDU_Start(void* self, void* mi) {
    if (orig_AVDU_Start) orig_AVDU_Start(self, mi);
    try {
        if (!self) return;
        void* txt = *(void**)((uint8_t*)self + 0x18);   // Text versionText
        if (!txt) return;
        Class app("UnityEngine", "Application", Image("UnityEngine.CoreModule.dll"));
        Method<BNM::Structures::Mono::String*> gv(app.GetMethod("get_version", 0));
        if (!gv.IsValid()) return;
        auto* ver = gv();
        if (!ver) return;
        Class txtc("UnityEngine.UI", "Text", Image("UnityEngine.UI.dll"));
        Method<void> st(txtc.GetMethod("set_text", 1));
        if (!st.IsValid()) return;
        st.SetInstance((IL2CPP::Il2CppObject*)txt);
        st(ver);
    } catch (...) {}
}
static void InstallVersionLabelClean() {
    try {
        Class c("", "ApplicationVersionDisplayUI", Image("Assembly-CSharp.dll"));
        if (!c.IsValid()) { BNM_FileCheckpoint("VerLabel: class NOT FOUND"); return; }
        MethodBase m = c.GetMethod("Start", 0);
        if (m.IsValid()) { BasicHook(m, hook_AVDU_Start, orig_AVDU_Start);
                           BNM_FileCheckpoint("VerLabel: Start() hooked (clean version)"); }
        else BNM_FileCheckpoint("VerLabel: Start(0) NOT FOUND");
    } catch (...) { BNM_FileCheckpoint("VerLabel: EXCEPTION"); }
}

// ---------------------------------------------------------------------
//  Backend redirect. In 1.19.0 the API base URL was plaintext in
//  global-metadata.dat and redirected by an in-place string patch. In
//  1.31.0 it is decrypted at runtime, so we instead hook the name-stable
//  framework entry points every HTTP request funnels through, LOG the full
//  (decrypted) URL for diagnosis, and rewrite the Yeeps execute-api gateway
//  host to the self-hosted server. The server strips the /xxxxxxxxxxxxxxxxxx
//  prefix (main.py strip_apk_prefix) and routes normally, exactly as the
//  1.19.0-blaze client's patched URL did.
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
//  Build identity + integrity tag. Computed once at startup from the
//  native libraries actually loaded on the headset and sent to the server
//  in the path of every API-gateway request (login etc.) as
//  /c/<kBuildId>.<fnv64(libil2cpp.so)>.<fnv64(libBNMExplorer.so)>/
//  The server records it per account and blocks wrong/old/modified builds.
//  Bump kBuildId for every APK you distribute.
// ---------------------------------------------------------------------
static const char* kBuildId = "kit2";
static std::string g_buildTag = std::string(kBuildId) + ".pending.pending";

static uint64_t Fnv1aFile(const char* path) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return 0;
    uint64_t h = 1469598103934665603ULL;
    std::vector<uint8_t> buf(1 << 20);
    ssize_t n;
    while ((n = read(fd, buf.data(), buf.size())) > 0)
        for (ssize_t i = 0; i < n; i++) { h ^= buf[i]; h *= 1099511628211ULL; }
    close(fd);
    return h;
}
struct LibPaths { std::string il2cpp, bnm; };
static int FindLibsCb(struct dl_phdr_info* info, size_t, void* data) {
    auto* lp = (LibPaths*)data;
    if (!info->dlpi_name) return 0;
    std::string n = info->dlpi_name;
    if (n.size() >= 12 && n.compare(n.size() - 12, 12, "libil2cpp.so") == 0) lp->il2cpp = n;
    if (n.size() >= 17 && n.compare(n.size() - 17, 17, "libBNMExplorer.so") == 0) lp->bnm = n;
    return 0;
}
static void ComputeBuildTag() {
    LibPaths lp;
    dl_iterate_phdr(FindLibsCb, &lp);
    char tag[96];
    snprintf(tag, sizeof(tag), "%s.%016llx.%016llx", kBuildId,
             (unsigned long long)(lp.il2cpp.empty() ? 0 : Fnv1aFile(lp.il2cpp.c_str())),
             (unsigned long long)(lp.bnm.empty() ? 0 : Fnv1aFile(lp.bnm.c_str())));
    g_buildTag = tag;
    BNM_FileCheckpointF("BuildTag: %s", tag);
}

// ---------------------------------------------------------------------
//  Headset device key (131b14+). The game API has no player login of its
//  own, so every API URL carries /k/<key>/ and the server ties each account
//  to the headset that owns it (vr-server services/device_auth.py).
//  key = sha256("blaze-device-v1|" + Android ID): ANDROID_ID is per device +
//  per signing key, survives reinstalls, and is never sent raw. If it can't
//  be read, a random key is kept in the app's files dir instead.
// ---------------------------------------------------------------------
static JavaVM* g_vm = nullptr;
static std::string g_deviceKey;

namespace sha256mini {
static const uint32_t K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
static inline uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }
static std::string hex(const std::string& msg) {
    uint32_t h[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    std::vector<uint8_t> m(msg.begin(), msg.end());
    uint64_t bits = (uint64_t)m.size() * 8;
    m.push_back(0x80);
    while (m.size() % 64 != 56) m.push_back(0);
    for (int i = 7; i >= 0; i--) m.push_back((uint8_t)(bits >> (i * 8)));
    for (size_t off = 0; off < m.size(); off += 64) {
        uint32_t w[64];
        for (int i = 0; i < 16; i++)
            w[i] = (uint32_t)m[off + 4*i] << 24 | (uint32_t)m[off + 4*i + 1] << 16 |
                   (uint32_t)m[off + 4*i + 2] << 8 | (uint32_t)m[off + 4*i + 3];
        for (int i = 16; i < 64; i++) {
            uint32_t s0 = rotr(w[i-15], 7) ^ rotr(w[i-15], 18) ^ (w[i-15] >> 3);
            uint32_t s1 = rotr(w[i-2], 17) ^ rotr(w[i-2], 19) ^ (w[i-2] >> 10);
            w[i] = w[i-16] + s0 + w[i-7] + s1;
        }
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
        for (int i = 0; i < 64; i++) {
            uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            uint32_t ch = (e & f) ^ (~e & g);
            uint32_t t1 = hh + S1 + ch + K[i] + w[i];
            uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t t2 = S0 + mj;
            hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    char out[65];
    for (int i = 0; i < 8; i++) snprintf(out + i * 8, 9, "%08x", h[i]);
    return std::string(out, 64);
}
}  // namespace sha256mini

static std::string ReadAndroidId() {
    if (!g_vm) return "";
    JNIEnv* env = nullptr;
    bool attached = false;
    if (g_vm->GetEnv((void**)&env, JNI_VERSION_1_6) != JNI_OK) {
        if (g_vm->AttachCurrentThread(&env, nullptr) != JNI_OK) return "";
        attached = true;
    }
    std::string id;
    auto clear = [&]() { if (env->ExceptionCheck()) { env->ExceptionClear(); return true; } return false; };
    do {
        jclass at = env->FindClass("android/app/ActivityThread");
        if (clear() || !at) break;
        jmethodID cur = env->GetStaticMethodID(at, "currentApplication", "()Landroid/app/Application;");
        if (clear() || !cur) break;
        jobject app = env->CallStaticObjectMethod(at, cur);
        if (clear() || !app) break;
        jclass ctx = env->FindClass("android/content/Context");
        if (clear() || !ctx) break;
        jmethodID gcr = env->GetMethodID(ctx, "getContentResolver", "()Landroid/content/ContentResolver;");
        if (clear() || !gcr) break;
        jobject cr = env->CallObjectMethod(app, gcr);
        if (clear() || !cr) break;
        jclass sec = env->FindClass("android/provider/Settings$Secure");
        if (clear() || !sec) break;
        jmethodID gs = env->GetStaticMethodID(sec, "getString",
                           "(Landroid/content/ContentResolver;Ljava/lang/String;)Ljava/lang/String;");
        if (clear() || !gs) break;
        jstring name = env->NewStringUTF("android_id");
        auto js = (jstring)env->CallStaticObjectMethod(sec, gs, cr, name);
        if (clear() || !js) break;
        const char* c = env->GetStringUTFChars(js, nullptr);
        if (c) { id = c; env->ReleaseStringUTFChars(js, c); }
    } while (false);
    if (attached) g_vm->DetachCurrentThread();
    return id;
}

static std::string RandomKeyFile() {
    // fallback: a random key kept in /data/data/<package>/files
    char pkg[128] = {0};
    int fd = open("/proc/self/cmdline", O_RDONLY);
    if (fd >= 0) { read(fd, pkg, sizeof(pkg) - 1); close(fd); }
    std::string path = std::string("/data/data/") + pkg + "/files/blaze_device_key";
    std::string key;
    fd = open(path.c_str(), O_RDONLY);
    if (fd >= 0) {
        char buf[65] = {0};
        if (read(fd, buf, 64) == 64) key.assign(buf, 64);
        close(fd);
    }
    if (key.size() == 64) return key;
    uint8_t rnd[32] = {0};
    fd = open("/dev/urandom", O_RDONLY);
    if (fd >= 0) { read(fd, rnd, sizeof(rnd)); close(fd); }
    key = sha256mini::hex(std::string((const char*)rnd, sizeof(rnd)));
    fd = open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd >= 0) { write(fd, key.data(), key.size()); close(fd); }
    return key;
}

// =====================================================================
//  Self-host kit settings (Yeeps-1.31.0-blaze kit). Read once at load:
//   1) the settings file inside the APK: lib/arm64-v8a/libblazeconfig.so (a
//      plain text file; Android extracts lib*.so files next to this library,
//      so no asset API is needed). tools/configure_apk.py writes it.
//   2) optional override on the headset (handy for testing):
//      /sdcard/Android/data/com.TrassGames.Yeeps/files/blaze-config.txt
//  Lines are key=value; '#' starts a comment. Keys:
//   server               base URL of the host's server, e.g. https://x.trycloudflare.com
//                        or http://192.168.1.20:8000 (plain HTTP is allowed)
//   photon_app_id        the host's Photon PUN (Realtime) app id
//   photon_voice_app_id  the host's Photon Voice app id
//   photon_region        optional fixed region (us, usw, eu, ...); blank keeps the APK's
// =====================================================================
static std::map<std::string, std::string> g_kitCfg;
static std::string KitCfg(const char* key, const char* def = "") {
    auto it = g_kitCfg.find(key);
    return (it == g_kitCfg.end() || it->second.empty()) ? std::string(def) : it->second;
}
static std::string KitTrim(const std::string& s) {
    size_t a = s.find_first_not_of(" \t"), b = s.find_last_not_of(" \t");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}
static void ParseKitCfg(const std::string& text) {
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        size_t h = line.find('#');
        if (h != std::string::npos) line = line.substr(0, h);
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        std::string k = KitTrim(line.substr(0, eq));
        if (!k.empty()) g_kitCfg[k] = KitTrim(line.substr(eq + 1));
    }
}
static bool ReadSmallFile(const std::string& path, std::string& out) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;
    char buf[4096];
    size_t n;
    out.clear();
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0 && out.size() < 65536) out.append(buf, n);
    fclose(f);
    return true;
}
static void LoadKitConfig() {
    std::string text, from = "none";
    Dl_info info{};
    if (dladdr((void*)&LoadKitConfig, &info) && info.dli_fname) {
        std::string dir(info.dli_fname);
        size_t cut = dir.rfind('/');
        if (cut != std::string::npos && ReadSmallFile(dir.substr(0, cut) + "/libblazeconfig.so", text)) {
            ParseKitCfg(text);
            from = "apk";
        }
    }
    if (ReadSmallFile("/sdcard/Android/data/com.TrassGames.Yeeps/files/blaze-config.txt", text)) {
        ParseKitCfg(text);
        from += "+headset";
    }
    std::string srv = KitCfg("server");
    while (!srv.empty() && srv.back() == '/') srv.pop_back();
    g_kitCfg["server"] = srv;
    BNM_FileCheckpointF("Kit: settings from %s, server=%s photon=%s voice=%s region=%s", from.c_str(),
                        srv.empty() ? "(none)" : srv.c_str(),
                        KitCfg("photon_app_id").empty() ? "(none)" : "set",
                        KitCfg("photon_voice_app_id").empty() ? "(none)" : "set",
                        KitCfg("photon_region", "(apk default)").c_str());
}
static std::string KitServer() { return KitCfg("server", "http://127.0.0.1:8000"); }
static std::string KitUrl(const char* path) { return KitServer() + path; }

static void ComputeDeviceKey() {
    std::string aid = ReadAndroidId();
    if (!aid.empty()) {
        g_deviceKey = sha256mini::hex("blaze-device-v1|" + aid);
        BNM_FileCheckpoint("DeviceKey: from android id");
    } else {
        g_deviceKey = RandomKeyFile();
        BNM_FileCheckpoint("DeviceKey: random (android id unavailable)");
    }
}

// The kit server strips this fixed path prefix (main.py _PATCH_PREFIX).
static const char* kSelfPrefix = "/xxxxxxxxxxxxxxxxxx";
static bool RewriteBackendUrl(const std::string& in, std::string& out) {
    // Yeeps uses THREE AWS backends, all of which must go to our proxy:
    //   *.execute-api.*.amazonaws.com  -> API Gateway (login/account)
    //   lambda.*.amazonaws.com         -> direct Lambda g2_* calls (room/game data)
    //   dynamodb.*.amazonaws.com       -> direct DynamoDB (room map nodes, etc.)
    // Asset CDNs (s3/cloudfront) are left alone so real game assets still load.
    auto sch = in.find("://");
    if (sch == std::string::npos) return false;
    size_t hostStart = sch + 3;
    size_t pathStart = in.find('/', hostStart);
    std::string host = (pathStart == std::string::npos)
                     ? in.substr(hostStart) : in.substr(hostStart, pathStart - hostStart);
    std::string path = (pathStart == std::string::npos) ? "/" : in.substr(pathStart);
    bool aws = host.find(".amazonaws.com") != std::string::npos;
    if (!aws) return false;
    bool match = (host.find(".execute-api.") != std::string::npos)
              || (host.rfind("lambda.", 0) == 0)
              || (host.rfind("dynamodb.", 0) == 0);
    if (!match) return false;
    // API-gateway URLs (login etc.) are rewritten whole, so they carry the
    // build tag. Lambda/DynamoDB are host-only SDK endpoints the SDK builds
    // paths onto; leave those untagged.
    std::string base = KitServer() + kSelfPrefix;
    if (g_deviceKey.size() == 64) base += "/k/" + g_deviceKey;   // who's asking (device_auth.py)
    if (host.find(".execute-api.") != std::string::npos)
        out = base + "/c/" + g_buildTag + path;
    else
        out = base + path;   // keep path + query
    return true;
}
static void* MaybeRewrite(void* urlObj, [[maybe_unused]] const char* tag) {
    try {
        if (!urlObj) return urlObj;
        std::string u = ((BNM::Structures::Mono::String*)urlObj)->str();
        std::string nu;
        if (RewriteBackendUrl(u, nu))
            return CreateMonoString(nu.c_str());
    } catch (...) {}
    return urlObj;
}
static void (*orig_UWR_seturl)(void*, void*, IL2CPP::MethodInfo*) = nullptr;
static void hook_UWR_seturl(void* self, void* url, IL2CPP::MethodInfo* mi) {
    void* pass = MaybeRewrite(url, "UWR.set_url");
    if (orig_UWR_seturl) orig_UWR_seturl(self, pass, mi);
}
static void (*orig_Uri_ctor)(void*, void*, IL2CPP::MethodInfo*) = nullptr;
static void hook_Uri_ctor(void* self, void* uriStr, IL2CPP::MethodInfo* mi) {
    void* pass = MaybeRewrite(uriStr, "Uri.ctor");
    if (orig_Uri_ctor) orig_Uri_ctor(self, pass, mi);
}
// ---------------------------------------------------------------------
//  Photon room joins carry the headset key too (131b15+). Photon Cloud
//  calls our custom-auth URL with the client's AuthGetParameters, so we add
//  dk=<device key> right before the client authenticates; the server then
//  refuses a join that claims someone else's account (device_auth.py).
// ---------------------------------------------------------------------
static void AddDeviceKeyToAuth(void* authValues) {
    try {
        if (!authValues || g_deviceKey.size() != 64) return;
        Class av("Photon.Realtime", "AuthenticationValues");
        Method<BNM::Structures::Mono::String*> getP(av.GetMethod("get_AuthGetParameters", 0));
        Method<void> setP(av.GetMethod("set_AuthGetParameters", 1));
        if (!getP.IsValid() || !setP.IsValid()) return;
        auto* cur = getP[(Il2CppObject*)authValues]();
        std::string q = cur ? cur->str() : "";
        if (q.find("dk=") != std::string::npos) return;
        if (!q.empty()) q += "&";
        q += "dk=" + g_deviceKey;
        setP[(Il2CppObject*)authValues](CreateMonoString(q.c_str()));
    } catch (...) {}
}
static bool (*orig_OpAuth)(void*, void*, void*, void*, void*, bool, IL2CPP::MethodInfo*) = nullptr;
static bool hook_OpAuth(void* self, void* appId, void* appVer, void* av, void* region, bool stats, IL2CPP::MethodInfo* mi) {
    AddDeviceKeyToAuth(av);
    return orig_OpAuth ? orig_OpAuth(self, appId, appVer, av, region, stats, mi) : false;
}
static bool (*orig_OpAuthOnce)(void*, void*, void*, void*, void*, int32_t, int32_t, IL2CPP::MethodInfo*) = nullptr;
static bool hook_OpAuthOnce(void* self, void* appId, void* appVer, void* av, void* region, int32_t enc, int32_t proto, IL2CPP::MethodInfo* mi) {
    AddDeviceKeyToAuth(av);
    return orig_OpAuthOnce ? orig_OpAuthOnce(self, appId, appVer, av, region, enc, proto, mi) : false;
}
static void InstallPhotonAuthKey() {
    try {
        Class lbp("Photon.Realtime", "LoadBalancingPeer");
        if (!lbp.IsValid()) { BNM_FileCheckpoint("[auth] LoadBalancingPeer NOT FOUND"); return; }
        MethodBase a = lbp.GetMethod("OpAuthenticate", 5);
        if (a.IsValid()) { BasicHook(a, hook_OpAuth, orig_OpAuth); BNM_FileCheckpoint("[auth] OpAuthenticate hooked"); }
        else BNM_FileCheckpoint("[auth] OpAuthenticate(5) NOT FOUND");
        MethodBase b = lbp.GetMethod("OpAuthenticateOnce", 6);
        if (b.IsValid()) { BasicHook(b, hook_OpAuthOnce, orig_OpAuthOnce); BNM_FileCheckpoint("[auth] OpAuthenticateOnce hooked"); }
        else BNM_FileCheckpoint("[auth] OpAuthenticateOnce(6) NOT FOUND");
    } catch (...) { BNM_FileCheckpoint("[auth] EXC"); }
}

static void InstallBackendRedirect() {
    try {
        Class uwr("UnityEngine.Networking", "UnityWebRequest");
        if (uwr.IsValid()) {
            MethodBase m = uwr.GetMethod("set_url", 1);
            if (m.IsValid()) { BasicHook(m, hook_UWR_seturl, orig_UWR_seturl);
                               BNM_FileCheckpoint("[net] UnityWebRequest.set_url hooked"); }
            else BNM_FileCheckpoint("[net] UWR.set_url(1) NOT FOUND");
        } else BNM_FileCheckpoint("[net] UnityWebRequest class NOT FOUND");
    } catch (...) { BNM_FileCheckpoint("[net] EXC UWR"); }
    try {
        Class uri("System", "Uri");
        if (uri.IsValid()) {
            MethodBase m = uri.GetMethod(".ctor", 1);
            if (m.IsValid()) { BasicHook(m, hook_Uri_ctor, orig_Uri_ctor);
                               BNM_FileCheckpoint("[net] System.Uri.ctor(1) hooked"); }
            else BNM_FileCheckpoint("[net] Uri.ctor(1) NOT FOUND");
        } else BNM_FileCheckpoint("[net] System.Uri class NOT FOUND");
    } catch (...) { BNM_FileCheckpoint("[net] EXC Uri"); }
}

// ---------------------------------------------------------------------
//  il2cpp-attach entry point (BNM). Kept minimal for 1.19.0: the BNM
//  Explorer HTTP server (so the game can be inspected/edited live) plus
//  the Debug.Log capture that feeds the Explorer's /logs view. The core
//  entitlement fix runs from JNI_OnLoad and does not depend on il2cpp.
// ---------------------------------------------------------------------
// ---------------------------------------------------------------------
//  1.31 account-validation bypass (release). The account coroutine
//  (RVA 0x287FD94) runs a client-side integrity check, "Gate B"
//  (RVA 0x256C0C8), over account fields we legitimately serve differently
//  on the private server, so it reports "Failed to validate account data".
//  Run the real check (side effects kept) and force it to pass, but only
//  while the account coroutine is active; other callers get the real result.
//  RVAs are libil2cpp file offsets; base = il2cpp_raise_exception - 0xFDD86C.
// ---------------------------------------------------------------------
static std::atomic<bool> g_inAcctCoroutine{false};
static void* (*orig_AcctCoroutine)(void*, void*, void*, void*, void*, void*, void*, void*) = nullptr;
static void* hook_AcctCoroutine(void* a0, void* a1, void* a2, void* a3, void* a4, void* a5, void* a6, void* a7) {
    g_inAcctCoroutine.store(true);
    void* r = orig_AcctCoroutine ? orig_AcctCoroutine(a0, a1, a2, a3, a4, a5, a6, a7) : nullptr;
    g_inAcctCoroutine.store(false);
    return r;
}
static uint64_t (*orig_AcctValidate)(void*, void*, void*, void*, void*, void*, void*, void*) = nullptr;
static uint64_t hook_AcctValidate(void* a0, void* a1, void* a2, void* a3, void* a4, void* a5, void* a6, void* a7) {
    uint64_t r = orig_AcctValidate ? orig_AcctValidate(a0, a1, a2, a3, a4, a5, a6, a7) : 0;
    if (g_inAcctCoroutine.load() && (r & 1) == 0) return 1;
    return r;
}
static void InstallAccountValidateBypass() {
    void* exp = dlsym(BNM::Internal::il2cppLibraryHandle, "il2cpp_raise_exception");
    if (!exp) { BNM_FileCheckpoint("AcctBypass: no il2cpp base"); return; }
    uint8_t* base = (uint8_t*)exp - 0xFDD86CULL;
    void* tr = nullptr;
    if (DobbyHook(base + 0x287FD94ULL, (dobby_dummy_func_t)hook_AcctCoroutine, (dobby_dummy_func_t*)&tr) == 0)
        orig_AcctCoroutine = (void*(*)(void*, void*, void*, void*, void*, void*, void*, void*))tr;
    tr = nullptr;
    if (DobbyHook(base + 0x256C0C8ULL, (dobby_dummy_func_t)hook_AcctValidate, (dobby_dummy_func_t*)&tr) == 0)
        orig_AcctValidate = (uint64_t(*)(void*, void*, void*, void*, void*, void*, void*, void*))tr;
    BNM_FileCheckpointF("AcctBypass: coroutine=%d validate=%d", orig_AcctCoroutine != nullptr, orig_AcctValidate != nullptr);
}
// =====================================================================
//  Native helpers + the main-thread tick (AccountOnlineManager.Update), which
//  applies the host's Photon voice app id (KitPhotonTick). Picture blocks
//  (tools/data/add_picture_block.py) get their material in
//  MapObject.SetSharedMaterials.
// =====================================================================
static void* (*p_gchandle_target)(void*) = nullptr;
static void (*p_gchandle_free)(void*) = nullptr;
static void* Pin(void* obj) {                 // keep a native-held object alive
    if (!obj || !p_gchandle_new) return nullptr;
    return p_gchandle_new(obj, false);
}
static void Unpin(void* h) { if (h && p_gchandle_free) p_gchandle_free(h); }

static std::string MonoStr(void* s) {
    if (!s) return "";
    try { return ((BNM::Structures::Mono::String*)s)->str(); } catch (...) { return ""; }
}
static bool Alive(void* obj) {
    if (!obj) return false;
    try {
        Method<bool> m(Class("UnityEngine", "Object").GetMethod("op_Implicit", 1));
        return m.IsValid() ? m((Il2CppObject*)obj) : true;
    } catch (...) { return false; }
}
// Pick an overload by exact parameter type names (e.g. Get(string) vs Get(Uri)).
static MethodBase FindOverload(Class c, const char* name, std::vector<std::string> types) {
    for (auto& m : c.GetMethods(false)) {
        auto* mi = m.GetInfo();
        if (!mi || !mi->name || strcmp(mi->name, name) != 0 || mi->parameters_count != types.size()) continue;
        bool ok = true;
        for (size_t i = 0; i < types.size() && ok; i++)
            ok = mi->parameters && mi->parameters[i] && typeName(mi->parameters[i]) == types[i];
        if (ok) return m;
    }
    return MethodBase();
}
static std::atomic<int> g_remoteLogN{0};
#define RLOG(...) do { if (g_remoteLogN.fetch_add(1) < 40) BNM_FileCheckpointF(__VA_ARGS__); } while (0)
static void* (*p_object_new)(void*) = nullptr;
static void* (*p_array_new)(void*, uintptr_t) = nullptr;
// Picture blocks: slot 1 = a fixed picture material shipped in the game data
// (tools/data/add_picture_block.py), registered at the Resources path
// "blazepic/mat_<block key, lowercase>". Every block key is looked up once.
static std::map<std::string, void*> g_pictureMats;     // path -> pinned material
static std::map<std::string, std::string> g_pictureKeyPath;   // block key -> path ("" = not a picture block)
static Il2CppObject* PictureMaterial(const std::string& path) {
    auto f = g_pictureMats.find(path);
    if (f != g_pictureMats.end() && p_gchandle_target) {
        auto* m = (Il2CppObject*)p_gchandle_target(f->second);
        if (m && Alive(m)) return m;
        Unpin(f->second); g_pictureMats.erase(f);
    }
    Method<Il2CppObject*> load(FindOverload(Class("UnityEngine", "Resources"), "Load", {"System.String"}));
    if (!load.IsValid()) return nullptr;
    Il2CppObject* m = load(CreateMonoString(path.c_str()));
    if (m) g_pictureMats[path] = Pin(m);
    return m;
}
static std::string PicturePathFor(const std::string& key) {
    auto it = g_pictureKeyPath.find(key);
    if (it != g_pictureKeyPath.end()) return it->second;
    std::string lower = key;
    for (auto& ch : lower) ch = (char)tolower((unsigned char)ch);
    std::string path = "blazepic/mat_" + lower;
    std::string found = PictureMaterial(path) ? path : std::string();
    g_pictureKeyPath[key] = found;
    return found;
}
static void (*orig_MO_SetSharedMaterials)(void*, void*, void*) = nullptr;
static void hook_MO_SetSharedMaterials(void* self, void* arr, void* mi) {
    void* pass = arr;
    try {
        if (self && arr && p_array_new) {
            Class mo("", "MapObject", Image("Assembly-CSharp.dll"));
            std::string key = MonoStr(Method<Il2CppObject*>(mo.GetMethod("get_key", 0))[(Il2CppObject*)self]());
            if (key.empty()) {
                Il2CppObject* go = Method<Il2CppObject*>(Class("UnityEngine", "Component").GetMethod("get_gameObject", 0))[(Il2CppObject*)self]();
                if (go) key = MonoStr(Method<Il2CppObject*>(Class("UnityEngine", "Object").GetMethod("get_name", 0))[go]());
                size_t c = key.find("(Clone)"); if (c != std::string::npos) key = key.substr(0, c);
            }
            std::string picPath = key.empty() ? std::string() : PicturePathFor(key);
            if (!picPath.empty()) {
                uintptr_t n = *(uintptr_t*)((uint8_t*)arr + 0x18);     // Il2CppArray max_length
                Il2CppObject* m = PictureMaterial(picPath);
                if (n >= 2 && m) {
                    void* copy = p_array_new(Class("UnityEngine", "Material").GetClass(), n);
                    if (copy) {
                        memcpy((uint8_t*)copy + 0x20, (uint8_t*)arr + 0x20, n * sizeof(void*));
                        ((void**)((uint8_t*)copy + 0x20))[1] = m;
                        pass = copy;
                    }
                }
                static int plogged = 0;
                if (plogged++ < 6) BNM_FileCheckpointF("Remote: picture material for %s (n=%d, mat=%d)", key.c_str(), (int)n, m != nullptr);
            }
        }
    } catch (...) {}
    if (orig_MO_SetSharedMaterials) orig_MO_SetSharedMaterials(self, pass, mi);
}

static void (*orig_TickUpdate)(void*, void*) = nullptr;
// ---- Photon app ids from the kit settings --------------------------------
// The APK data still holds the app ids it was built with (PhotonServerSettings
// and the Photon Voice "VoiceAppSettings" asset). The kit replaces them with
// the host's ids: PUN's settings the first time the game reads them (the
// PhotonNetwork.PhotonServerSettings getter, before any connect), and the voice
// copies from the per-frame tick, because those load later. Ids are ALWAYS
// replaced: an empty id makes Photon refuse to connect instead of quietly using
// whoever's app the APK was built with.
static void (*p_wbarrier_set)(void*, void**, void*) = nullptr;
static Il2CppObject* KitGetObjField(Il2CppObject* obj, Class cls, const char* name) {
    if (!obj || !cls.IsValid()) return nullptr;
    FieldBase f = cls.GetField(name);
    if (!f.IsValid() || !f.GetInfo()) return nullptr;
    return *(Il2CppObject**)((uint8_t*)obj + f.GetInfo()->offset);
}
static bool KitSetObjField(Il2CppObject* obj, Class cls, const char* name, Il2CppObject* val) {
    if (!obj || !cls.IsValid()) return false;
    FieldBase f = cls.GetField(name);
    if (!f.IsValid() || !f.GetInfo()) return false;
    void** slot = (void**)((uint8_t*)obj + f.GetInfo()->offset);
    if (p_wbarrier_set) p_wbarrier_set(obj, slot, val); else *slot = val;
    return true;
}
static void KitApplyAppSettings(Il2CppObject* appSettings, const char* what) {
    if (!appSettings) return;
    Class as("Photon.Realtime", "AppSettings");
    std::string rt = KitCfg("photon_app_id"), vo = KitCfg("photon_voice_app_id"), rg = KitCfg("photon_region");
    bool ok = KitSetObjField(appSettings, as, "AppIdRealtime", (Il2CppObject*)CreateMonoString(rt.c_str()));
    ok = KitSetObjField(appSettings, as, "AppIdVoice", (Il2CppObject*)CreateMonoString(vo.c_str())) && ok;
    if (!rg.empty()) KitSetObjField(appSettings, as, "FixedRegion", (Il2CppObject*)CreateMonoString(rg.c_str()));
    BNM_FileCheckpointF("Kit: Photon ids -> %s (%s)", what, ok ? "ok" : "FIELDS NOT FOUND");
}
static std::atomic<bool> g_kitPunApplied{false};
static void* (*orig_PN_GetSettings)(void*) = nullptr;
static void* hook_PN_GetSettings(void* mi) {
    void* ss = orig_PN_GetSettings ? orig_PN_GetSettings(mi) : nullptr;
    if (ss && !g_kitPunApplied.exchange(true)) {
        try { KitApplyAppSettings(KitGetObjField((Il2CppObject*)ss, Class("Photon.Pun", "ServerSettings"), "AppSettings"),
                                  "PhotonServerSettings"); }
        catch (...) { BNM_FileCheckpoint("Kit: Photon ids EXCEPTION (PUN)"); }
    }
    return ss;
}
// Second layer: every Photon connection (PUN and Voice both go through
// LoadBalancingClient.ConnectUsingSettings(AppSettings)) gets the host's ids
// written into the settings it is about to use, right before connecting. The
// client picks AppIdRealtime or AppIdVoice by its own type, so both are set.
static bool (*orig_LBC_Connect)(void*, void*, void*) = nullptr;
static bool hook_LBC_Connect(void* self, void* appSettings, void* mi) {
    try { KitApplyAppSettings((Il2CppObject*)appSettings, "LoadBalancingClient.ConnectUsingSettings"); }
    catch (...) { BNM_FileCheckpoint("Kit: Photon ids EXCEPTION (connect)"); }
    return orig_LBC_Connect ? orig_LBC_Connect(self, appSettings, mi) : false;
}
static std::set<void*> g_kitVoiceDone;
static double g_kitVoiceNext = 0;
static void KitPhotonTick() {
    double now = nowSeconds();
    if (now < g_kitVoiceNext) return;
    g_kitVoiceNext = now + 2.0;
    Method<Il2CppObject*> findAll(FindOverload(Class("UnityEngine", "Resources"), "FindObjectsOfTypeAll", {"System.Type"}));
    if (!findAll.IsValid()) return;
    struct Src { const char* ns; const char* cls; const char* field; } srcs[] = {
        {"Photon.Voice", "PhotonAppSettings", "AppSettings"},      // the VoiceAppSettings asset
        {"Photon.Voice.Unity", "VoiceConnection", "Settings"},     // a voice connection's own copy
    };
    for (auto& sdef : srcs) {
        Class c(sdef.ns, sdef.cls);
        if (!c.IsValid()) continue;
        auto* arr = (uint8_t*)findAll((Il2CppObject*)c.GetMonoType());
        if (!arr) continue;
        uintptr_t n = *(uintptr_t*)(arr + 0x18);
        for (uintptr_t i = 0; i < n; i++) {
            void* o = ((void**)(arr + 0x20))[i];
            if (!o || g_kitVoiceDone.count(o)) continue;
            g_kitVoiceDone.insert(o);
            KitApplyAppSettings(KitGetObjField((Il2CppObject*)o, c, sdef.field), sdef.cls);
        }
    }
}
static void InstallKitPhoton() {
    try {
        p_wbarrier_set = (void(*)(void*, void**, void*))BNM_dlsym(BNM::Internal::il2cppLibraryHandle, "il2cpp_gc_wbarrier_set_field");
        Class pn("Photon.Pun", "PhotonNetwork");
        MethodBase g = pn.IsValid() ? pn.GetMethod("get_PhotonServerSettings", 0) : MethodBase();
        if (g.IsValid()) { BasicHook(g, hook_PN_GetSettings, orig_PN_GetSettings); BNM_FileCheckpoint("Kit: PhotonServerSettings getter hooked"); }
        else BNM_FileCheckpoint("Kit: PhotonNetwork.get_PhotonServerSettings NOT FOUND");
        Class lbc("Photon.Realtime", "LoadBalancingClient");
        MethodBase c = lbc.IsValid() ? FindOverload(lbc, "ConnectUsingSettings", {"Photon.Realtime.AppSettings"}) : MethodBase();
        if (c.IsValid()) { BasicHook(c, hook_LBC_Connect, orig_LBC_Connect); BNM_FileCheckpoint("Kit: LoadBalancingClient.ConnectUsingSettings hooked"); }
        else BNM_FileCheckpoint("Kit: LoadBalancingClient.ConnectUsingSettings NOT FOUND");
    } catch (...) { BNM_FileCheckpoint("Kit: EXCEPTION installing Photon hook"); }
}

static void hook_TickUpdate(void* self, void* mi) {
    if (orig_TickUpdate) orig_TickUpdate(self, mi);
    try { KitPhotonTick(); } catch (...) {}
}

static void InstallTickAndPictures() {
    try {
        p_gchandle_new = (void*(*)(void*, bool))BNM_dlsym(BNM::Internal::il2cppLibraryHandle, "il2cpp_gchandle_new");
        p_gchandle_target = (void*(*)(void*))BNM_dlsym(BNM::Internal::il2cppLibraryHandle, "il2cpp_gchandle_get_target");
        p_gchandle_free = (void(*)(void*))BNM_dlsym(BNM::Internal::il2cppLibraryHandle, "il2cpp_gchandle_free");
        p_object_new = (void*(*)(void*))BNM_dlsym(BNM::Internal::il2cppLibraryHandle, "il2cpp_object_new");
        p_array_new = (void*(*)(void*, uintptr_t))BNM_dlsym(BNM::Internal::il2cppLibraryHandle, "il2cpp_array_new");
        Class aom("", "AccountOnlineManager", Image("Assembly-CSharp.dll"));
        MethodBase up = aom.IsValid() ? aom.GetMethod("Update", 0) : MethodBase();
        if (up.IsValid()) { BasicHook(up, hook_TickUpdate, orig_TickUpdate); BNM_FileCheckpoint("Remote: tick hooked (AccountOnlineManager.Update)"); }
        else BNM_FileCheckpoint("Remote: AccountOnlineManager.Update NOT FOUND");
        Class mapObj("", "MapObject", Image("Assembly-CSharp.dll"));
        MethodBase ssm = mapObj.IsValid() ? FindOverload(mapObj, "SetSharedMaterials", {"UnityEngine.Material[]"}) : MethodBase();
        if (ssm.IsValid()) { BasicHook(ssm, hook_MO_SetSharedMaterials, orig_MO_SetSharedMaterials); BNM_FileCheckpoint("Remote: MapObject.SetSharedMaterials hooked"); }
        else BNM_FileCheckpoint("Remote: MapObject.SetSharedMaterials NOT FOUND");
        BNM_FileCheckpointF("Remote: gchandle api %d/%d/%d", p_gchandle_new != nullptr, p_gchandle_target != nullptr, p_gchandle_free != nullptr);
    } catch (...) { BNM_FileCheckpoint("Remote: EXCEPTION installing"); }
}

// ---- News board buttons (131b11) ---------------------------------------
// ActiveNewsDisplay's UnityEvent targets (method names are real: UnityEvents
// call by name). Discord -> our server's invite; "Leave a review" -> the built-in
// mod menu (BNM Explorer web UI served on the headset at :8080; the label text
// is changed in the game data).
static const char* kDiscordInvite = "https://discord.gg/VhFmdAyXGk";   // fixed watermark: not host-configurable
static const char* kModMenuUrl = "http://localhost:8080";
static void OpenUrl(const char* url) {
    try {
        Method<void> o(FindOverload(Class("UnityEngine", "Application"), "OpenURL", {"System.String"}));
        if (o.IsValid()) o(CreateMonoString(url));
        BNM_FileCheckpointF("Buttons: OpenURL %s (%d)", url, (int)o.IsValid());
    } catch (...) { BNM_FileCheckpoint("Buttons: OpenURL EXCEPTION"); }
}
static void (*orig_OpenDiscord)(void*, void*) = nullptr;
static void hook_OpenDiscord(void*, void*) { OpenUrl(kDiscordInvite); }
static void (*orig_OpenReview)(void*, void*) = nullptr;
static void hook_OpenReview(void*, void*) { OpenUrl(kModMenuUrl); }
static void InstallNewsButtons() {
    try {
        Class nd("", "ActiveNewsDisplay", Image("Assembly-CSharp.dll"));
        MethodBase d = nd.IsValid() ? nd.GetMethod("OnOpenDiscordLinkButtonPressed", 0) : MethodBase();
        MethodBase r = nd.IsValid() ? nd.GetMethod("OnLeaveReviewLinkButtonPressed", 0) : MethodBase();
        if (d.IsValid()) BasicHook(d, hook_OpenDiscord, orig_OpenDiscord);
        if (r.IsValid()) BasicHook(r, hook_OpenReview, orig_OpenReview);
        BNM_FileCheckpointF("Buttons: discord hook=%d review->modmenu hook=%d", d.IsValid(), r.IsValid());
    } catch (...) { BNM_FileCheckpoint("Buttons: EXCEPTION installing"); }
}

// ---- BLAZE role blocks: MapObject.requiredRoleKey = "blaze" -------------
// MapObject.CanUseMapObject(roles) checks requiredRoleKey against a static
// NetworkManager list that this server never fills (it's empty for everyone),
// so even blaze holders were refused. For "blaze" blocks, also accept when the
// local account's own roleKeys (AccountData, from the server login) contain
// "blaze". The server only sends that role to people who have it.
static double g_blazeRoleCheckedAt = -100;
static bool g_hasBlazeRole = false;
static bool LocalHasRole(const char* role) {
    double now = nowSeconds();
    if (now - g_blazeRoleCheckedAt < 5.0) return g_hasBlazeRole;
    g_blazeRoleCheckedAt = now;
    bool found = false;
    try {
        Class am("", "AccountManager", Image("Assembly-CSharp.dll"));
        Il2CppObject* ad = Method<Il2CppObject*>(am.GetMethod("get_accountData", 0))();
        if (ad) {
            Il2CppClass* strK = Class("System", "String").GetClass();
            for (Class c(ad->klass); c.IsValid() && c.GetClass() && !found; c = c.GetParent()) {
                for (auto& fb : c.GetFields(false)) {
                    auto* fi = fb.GetInfo();
                    if (!fi || !fi->type || (fi->type->attrs & 0x10 /* static */)) continue;
                    if (typeName(fi->type).rfind("System.Collections.Generic.List", 0) != 0) continue;
                    auto* list = *(uint8_t**)((uint8_t*)ad + fi->offset);
                    if (!list) continue;
                    auto* items = *(uint8_t**)(list + 0x10);
                    int size = *(int*)(list + 0x18);
                    if (!items || size <= 0 || size > 4096) continue;
                    // Only List<string>: other lists (e.g. of ints/structs) hold
                    // raw values, and reading those as object pointers crashed.
                    auto* arrK = ((Il2CppObject*)items)->klass;
                    if (!arrK || arrK->element_class != strK) continue;
                    for (int i = 0; i < size && !found; i++) {
                        auto* e = ((Il2CppObject**)(items + 0x20))[i];
                        if (e && e->klass == strK && MonoStr(e) == role) found = true;
                    }
                }
                if (strcmp(c.GetClass()->name, "Object") == 0) break;
            }
        }
    } catch (...) {}
    g_hasBlazeRole = found;
    return found;
}
static std::atomic<int> g_cumLogN{0};
static bool (*orig_CanUseMapObject)(void*, void*, void*) = nullptr;
static bool hook_CanUseMapObject(void* self, void* roles, void* mi) {
    bool ok = orig_CanUseMapObject ? orig_CanUseMapObject(self, roles, mi) : true;
    if (ok || !self) return ok;
    std::string req;
    try { req = MonoStr(*(void**)((uint8_t*)self + 0x68)); } catch (...) {}
    bool allow = (req == "blaze") && LocalHasRole("blaze");
    if (g_cumLogN.fetch_add(1) < 20)
        BNM_FileCheckpointF("Roles: CanUseMapObject requires '%s' -> local blaze=%d -> %s",
                            req.c_str(), (int)g_hasBlazeRole, allow ? "ALLOW" : "refuse");
    return allow;
}
static void InstallBlazeRoleCheck() {
    try {
        Class mo("", "MapObject", Image("Assembly-CSharp.dll"));
        MethodBase m = mo.IsValid() ? mo.GetMethod("CanUseMapObject", 1) : MethodBase();
        if (m.IsValid()) { BasicHook(m, hook_CanUseMapObject, orig_CanUseMapObject); BNM_FileCheckpoint("Roles: CanUseMapObject hooked"); }
        else BNM_FileCheckpoint("Roles: CanUseMapObject NOT FOUND");
    } catch (...) { BNM_FileCheckpoint("Roles: EXCEPTION installing"); }
}

static void OnLoaded() {
    // PUBLIC BUILD: only the BNM Explorer + its Debug.Log feed. The Meta
    // entitlement/user-proof bypass is installed from JNI_OnLoad
    // (OvrHookPollThread) and does not depend on il2cpp attach. All the
    // credential/crypto/backtrace diagnostics are removed for distribution.
    __android_log_print(ANDROID_LOG_ERROR, "MYDBG", "OnLoaded fired (1.31.0) - il2cpp attach succeeded");
    BNM_FileCheckpoint("OnLoaded fired (1.31.0) - il2cpp attach succeeded");
    InstallGameAnalyticsBlock();           // kill third-party telemetry
    InstallVersionLabelClean();            // show just "1.31.0-blaze"
    ComputeBuildTag();                     // build id + native-lib hashes for the server
    ComputeDeviceKey();                    // headset key sent with every API call
    InstallPhotonAuthKey();                // ...and with every Photon room join
    InstallBackendRedirect();              // execute-api + lambda + dynamodb -> self-host
    InstallKitPhoton();                    // the host's Photon app ids (kit settings)
    InstallAccountValidateBypass();        // pass the client-side account integrity check
    InstallTickAndPictures();              // main-thread tick (voice app id) + picture blocks
    InstallBlazeRoleCheck();               // BLAZE blocks: placeable with the blaze role
    InstallNewsButtons();                  // news board: Discord -> our invite, Review -> mod menu
    hookDebugLog();                        // feed Explorer /logs
    std::thread(startServer).detach();     // BNM Explorer on 0.0.0.0:8080
}

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, [[maybe_unused]] void* reserved) {
    BNM_FileCheckpoint("=== JNI_OnLoad entered (1.31.0) ===");
    __android_log_print(ANDROID_LOG_ERROR, "MYDBG", "JNI_OnLoad entered (1.31.0)");
    JNIEnv* env;
    g_vm = vm;
    vm->GetEnv((void**)&env, JNI_VERSION_1_6);
    LoadKitConfig();                       // kit settings: server, Photon ids, Discord
    std::thread(OvrHookPollThread).detach();
    BNM::Loading::AddOnLoadedEvent(OnLoaded);
    BNM::Loading::TryLoadByJNI(env);
    __android_log_print(ANDROID_LOG_ERROR, "MYDBG", "JNI_OnLoad: TryLoadByJNI returned (1.31.0)");
    BNM_FileCheckpoint("=== JNI_OnLoad: TryLoadByJNI returned (1.31.0) ===");
    return JNI_VERSION_1_6;
}
