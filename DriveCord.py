import curses, requests, threading, math, os, json, random, time
from datetime import datetime
from queue import Queue

CONFIG_FILENAME="drivecord_config.json"
CHUNK_SIZE=5*1024*1024
tasks_queue=Queue()
active_tasks={}
active_tasks_lock= threading.Lock()
worker_threads=[]
STOP_WORKERS=False
token_validity_lock= threading.Lock()
token_validity_map={}

def load_config():
    if not os.path.exists(CONFIG_FILENAME):
        return {
            "server_id":"",
            "channel_id":"",
            "bot_tokens":[],
            "directories":{
                "name":"root",
                "files":[],
                "subdirs":[],
                "expanded":True
            },
            "chunk_size_mb":5
        }
    with open(CONFIG_FILENAME,"r",encoding="utf-8")as f:
        data=json.load(f)
    remove_incomplete_uploads(data)
    return data

def remove_incomplete_uploads(cfg):
    def dfs(d):
        newfiles=[]
        for f in d["files"]:
            if f.get("in_process",False):
                pass
            else:newfiles.append(f)
        d["files"]=newfiles
        for sb in d["subdirs"]:
            dfs(sb)
    dfs(cfg["directories"])
    save_config(cfg)

def save_config(cfg):
    with open(CONFIG_FILENAME,"w",encoding="utf-8")as f:
        json.dump(cfg,f,indent=4)

def apply_chunk_size(cfg):
    global CHUNK_SIZE
    val= cfg.get("chunk_size_mb",5)
    if val<5 or val>25: val=5
    CHUNK_SIZE= val*1024*1024

def set_chunk_size(cfg,new_val):
    try:
        num=int(new_val)
        if num<5 or num>25:
            num=5
        cfg["chunk_size_mb"]= num
    except:
        cfg["chunk_size_mb"]=5
    save_config(cfg)
    apply_chunk_size(cfg)

def valid_ids(sv,ch):
    if(not sv.isdigit())or(not ch.isdigit())or(not sv)or(not ch):
        return(False,"Invalid server/channel ID")
    return(True,None)

def test_token(tok):
    url="https://discord.com/api/v10/users/@me"
    hd={"Authorization":f"Bot {tok}"}
    try:
        r=requests.get(url,headers=hd)
        if r.status_code==200:return(True,None)
        return(False,f"HTTP {r.status_code}: {r.text}")
    except Exception as e:return(False,str(e))

def background_token_verifier(cfg):
    local={}
    for t in cfg["bot_tokens"]:
        ok,e=test_token(t)
        local[t]= ok
    with token_validity_lock:
        global token_validity_map
        token_validity_map= local

def build_tree_lines(dirnode,indent="",is_last=True):
    lines=[]
    prefix="└── " if is_last else"├── "
    expanded=dirnode.get("expanded",False)
    marker="[-]" if expanded else"[+]"
    line=f"{indent}{prefix}{marker} {dirnode['name']}"
    lines.append((line,"dir",dirnode))
    if not expanded:return lines
    cindent= indent+("    "if is_last else"│   ")
    tk= len(dirnode["subdirs"])+ len(dirnode["files"])
    idx=0
    for sb in dirnode["subdirs"]:
        last=(idx==tk-1)
        lines.extend(build_tree_lines(sb,cindent,last))
        idx+=1
    for f in dirnode["files"]:
        last=(idx==tk-1)
        pr="└── "if last else"├── "
        ln= f"{cindent}{pr}{f['file_name']} [ID={f['file_id']}]"
        lines.append((ln,"file",f))
        idx+=1
    return lines

def find_subdir(parent,name):
    for sb in parent["subdirs"]:
        if sb["name"]== name:return sb
    return None

def remove_file_record(cfg,fid):
    def dfs(d):
        filtered=[]
        for ff in d["files"]:
            if ff["file_id"]==fid:pass
            else:filtered.append(ff)
        d["files"]= filtered
        for sb in d["subdirs"]:
            dfs(sb)
    dfs(cfg["directories"])
    save_config(cfg)

def find_file(cfg,fid):
    def dfs(d):
        for ff in d["files"]:
            if ff["file_id"]==fid:return(d,ff)
        for sb in d["subdirs"]:
            r=dfs(sb)
            if r[0]is not None:return r
        return(None,None)
    return dfs(cfg["directories"])

def move_file_record(cfg,fid,pathlst):
    od,fo= find_file(cfg,fid)
    if not fo:return
    od["files"]=[x for x in od["files"] if x["file_id"]!=fid]
    root= cfg["directories"]
    c= root
    for nm in pathlst[1:]:
        sb= find_subdir(c,nm)
        if not sb:
            sb={"name":nm,"files":[],"subdirs":[],"expanded":False}
            c["subdirs"].append(sb)
        c= sb
    c["files"].append(fo)
    save_config(cfg)

def delete_dir(cfg,dirname):
    def dfs(par,node):
        if node["name"]==dirname and par is not None:
            par["subdirs"]=[x for x in par["subdirs"] if x is not node]
            return True
        for s in node["subdirs"]:
            if dfs(node,s):return True
        return False
    dfs(None,cfg["directories"])
    save_config(cfg)

def finalize_upload(cfg,fid):
    d,f= find_file(cfg,fid)
    if f:
        f["in_process"]=False
        save_config(cfg)

def chunk_file(fp):
    try:
        sz=os.path.getsize(fp)
        if sz<=0:
            yield(0,b'')
            return
        with open(fp,"rb")as f:
            idx=0
            while True:
                dd= f.read(CHUNK_SIZE)
                if not dd: break
                yield(idx,dd)
                idx+=1
    except:pass

def generate_fid():
    c="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return"".join(random.choice(c) for _ in range(8))

def up_chunk(token,channel,data,filename,fileid,ck):
    url=f"https://discord.com/api/v9/channels/{channel}/messages"
    hd={"Authorization":f"Bot {token}"}
    dt={"content":f"FILEID:{fileid} CHUNK:{ck}"}
    fs={"files[0]":(filename,data,"application/octet-stream")}
    try:
        r=requests.post(url,headers=hd,data=dt,files=fs)
        return(r.status_code in (200,201,204))
    except:return False

def fetch_msg(token,channel,lim=100):
    url=f"https://discord.com/api/v9/channels/{channel}/messages?limit={lim}"
    hd={"Authorization":f"Bot {token}"}
    try:
        r=requests.get(url,headers=hd)
        if r.status_code==200:return r.json()
    except:pass
    return[]

def dl_attach(url,token):
    hd={"Authorization":f"Bot {token}"}
    try:
        r=requests.get(url,headers=hd)
        if r.status_code==200:return r.content
    except:pass
    return None

def queue_download(cfg,fid):
    dt="download_"+str(random.randint(10000,99999))
    with active_tasks_lock:
        active_tasks[dt]={
            "type":"download",
            "file_id":fid,
            "progress":0,
            "total":0,
            "status":"Downloading...",
            "finished":False
        }
    tasks_queue.put((dt,active_tasks[dt]))

def do_chunk_upload(tid,inf,cfg):
    tk=inf["token"]
    if tk=="NO_TOKENS":tk=None
    if not tk:
        with active_tasks_lock:
            active_tasks[tid]["status"]="No tokens"
        return
    fid=inf["file_id"]
    cidx=inf["chunk_idx"]
    data=inf["chunk_data"]
    pf=inf["part_filename"]
    sc= up_chunk(tk,cfg["channel_id"],data,pf,fid,cidx)
    if not sc:
        with active_tasks_lock:
            active_tasks[tid]["status"]=f"Chunk {cidx} fail"
        return
    ft=inf["file_task_id"]
    with active_tasks_lock:
        if ft in active_tasks:
            active_tasks[ft]["progress"]+=1
            if active_tasks[ft]["progress"]== active_tasks[ft]["total"]:
                active_tasks[ft]["status"]="Upload complete"
                finalize_upload(cfg,fid)
        active_tasks[tid]["status"]=f"Chunk {cidx} done"

def do_download(tid,inf,cfg):
    fid= inf["file_id"]
    d,f= find_file(cfg,fid)
    if not f:
        with active_tasks_lock:
            active_tasks[tid]["status"]="File not found"
        return
    chunkcount= f["chunk_count"]
    fname= f["file_name"]
    folder="Drivecord Downloads"
    if not os.path.exists(folder):
        os.makedirs(folder)
    outp= os.path.join(folder,fname)
    got={}
    tokens= cfg["bot_tokens"]
    if not tokens:
        with active_tasks_lock:
            active_tasks[tid]["status"]="No tokens"
        return
    attempt=40
    start_time=time.time()
    downloaded=0
    bx=0
    while downloaded< chunkcount and attempt>0:
        attempt-=1
        tk= tokens[bx% len(tokens)]
        bx+=1
        msgs= fetch_msg(tk,cfg["channel_id"],100)
        if not msgs:continue
        for mm in msgs:
            if downloaded== chunkcount: break
            c= mm.get("content","")
            a= mm.get("attachments",[])
            if "FILEID:"in c and "CHUNK:"in c:
                try:
                    sp= c.split()
                    fi= sp[0].split("FILEID:")[1]
                    ck= int(sp[1].split("CHUNK:")[1])
                    if fi== fid and ck not in got:
                        if len(a)==1:
                            uu=a[0].get("url","")
                            dd= dl_attach(uu,tk)
                            if dd is not None:
                                got[ck]= dd
                                downloaded+=1
                                now= time.time()
                                elapsed= now- start_time
                                avg=0
                                if downloaded>0: avg= elapsed/downloaded
                                remain= chunkcount- downloaded
                                eta= int(remain* avg)
                                with active_tasks_lock:
                                    active_tasks[tid]["progress"]= downloaded
                                    active_tasks[tid]["total"]= chunkcount
                                    active_tasks[tid]["status"]= f"Downloading... ETA: {eta}s"
                except:pass
        time.sleep(0.01)
    if len(got)!= chunkcount:
        with active_tasks_lock:active_tasks[tid]["status"]="Download incomplete"
        return
    with open(outp,"wb")as out:
        for i in range(chunkcount):
            out.write(got[i])
    with active_tasks_lock:
        active_tasks[tid]["status"]= f"Download complete, file saved to: {outp}"

def ask_input(stdscr,title,prompt,init="",color_pair=0):
    curses.curs_set(1)
    h,w= stdscr.getmaxyx()
    bw=80
    bh=7
    y=(h-bh)//2
    x=(w-bw)//2
    wn= curses.newwin(bh,bw,y,x)
    wn.box()
    t_str= f" {title} "
    pos=(bw-len(t_str))//2
    safe_addstr(wn,0,pos,t_str,color_pair)
    safe_addstr(wn,2,2,prompt,color_pair)
    arr=list(init)
    while True:
        wn.move(3,2)
        for _ in range(bw-3):
            wn.addch(' ')
        wn.move(3,2)
        for ch in arr:
            wn.addch(ch)
        c= wn.getch()
        if c in[curses.KEY_ENTER,10,13]: break
        if c==27:
            curses.curs_set(0)
            return""
        if c in[curses.KEY_BACKSPACE,127]:
            if arr:
                arr.pop()
        elif 32<= c<=126:
            arr.append(chr(c))
    curses.curs_set(0)
    return "".join(arr).strip()

def confirm_delete_file(stdscr,name):
    txt= f"Are you sure you want to delete {name}? (y/n)"
    curses.curs_set(0)
    h,w= stdscr.getmaxyx()
    bw=80
    bh=5
    yy=(h-bh)//2
    xx=(w-bw)//2
    wn= curses.newwin(bh,bw,yy,xx)
    wn.box()
    safe_addstr(wn,2,2,txt,curses.color_pair(1))
    wn.refresh()
    while True:
        c= wn.getch()
        if c in [ord('y'),ord('Y')]: return True
        if c in [ord('n'),ord('N')]: return False

def confirm_delete_dir(stdscr,name):
    txt= f"Delete directory '{name}'? (y/n)"
    curses.curs_set(0)
    h,w= stdscr.getmaxyx()
    bw=80
    bh=5
    yy=(h-bh)//2
    xx=(w-bw)//2
    wn= curses.newwin(bh,bw,yy,xx)
    wn.box()
    safe_addstr(wn,2,2,txt,curses.color_pair(1))
    wn.refresh()
    while True:
        c=wn.getch()
        if c in [ord('y'),ord('Y')]: return True
        if c in [ord('n'),ord('N')]: return False

def error_popup(stdscr,msg,title="Notice",cp=1):
    curses.curs_set(0)
    lines= msg.split("\n")
    bw=100
    bh= len(lines)+6
    h,w= stdscr.getmaxyx()
    if bh>h-4: bh=h-4
    if bw>w-4: bw=w-4
    y=(h-bh)//2
    x=(w-bw)//2
    wn= curses.newwin(bh,bw,y,x)
    wn.box()
    t_str= f" {title} "
    pos=(bw-len(t_str))//2
    safe_addstr(wn,0,pos,t_str,cp)
    row=2
    for ln in lines:
        safe_addstr(wn,row,2,ln,cp)
        row+=1
        if row>=bh-1: break
    wn.refresh()
    wn.getch()

def safe_addstr(stdscr,y,x,txt,color=0):
    try:stdscr.addstr(y,x,txt,color)
    except:pass

def do_banner(stdscr):
    lines=[
r"  ________  ________  ___  ___      ___ _______   ________  ________  ________  ________      ",
r" |\   ___ \|\   __  \|\  \|\  \    /  /|\  ___ \ |\   ____\|\   __  \|\   __  \|\   ___ \     ",
r" \ \  \_|\ \ \  \|\  \ \  \ \  \  /  / | \   __/|\ \  \___|\ \  \|\  \ \  \|\  \ \  \_|\ \    ",
r"  \ \  \ \\ \ \   _  _\ \  \ \  \/  / / \ \  \_|/_\ \  \    \ \  \\\  \ \   _  _\ \  \ \\ \   ",
r"   \ \  \_\\ \ \  \\  \\ \  \ \    / /   \ \  \_|\ \ \  \____\ \  \\\  \ \  \\  \\ \  \_\\ \  ",
r"    \ \_______\ \__\\ _\\ \__\ \__/ /     \ \_______\ \_______\ \_______\ \__\\ _\\ \_______\ ",
r"     \|_______|\|__|\|__|\|__|\|__|/       \|_______|\|_______|\|_______|\|__|\|__|\|_______| ",
r"",
r"",
    ]
    stdscr.attron(curses.color_pair(3))
    row=1
    h,w= stdscr.getmaxyx()
    for ln in lines:
        safe_addstr(stdscr,row,max(0,(w-len(ln))//2),ln,curses.color_pair(3))
        row+=1
    stdscr.attroff(curses.color_pair(3))

def draw_tree(stdscr,arr,sel,top):
    h,w=stdscr.getmaxyx()
    capacity= h-12
    start=12
    used=0
    for i,(tx,typ,ref) in enumerate(arr[top:top+capacity]):
        y= start+i
        if y>=h-1: break
        arrow=">" if (top+i)==sel else" "
        line=f"{arrow} {tx}"
        if (top+i)==sel:
            safe_addstr(stdscr,y,2,line,curses.color_pair(4))
        else:
            safe_addstr(stdscr,y,2,line)
        used=y
    return used+1

def show_active_tasks(stdscr,used):
    with active_tasks_lock:
        tasks_list= list(active_tasks.values())
    row= used+2
    text_label="[Active Uploads/Downloads] Press R to refresh"
    safe_addstr(stdscr,row,4,text_label,curses.color_pair(5))
    row+=1
    for t in tasks_list:
        if t.get("finished"): continue
        ty= t["type"].upper()
        st= t["status"]
        pg= t.get("progress",0)
        tot= t.get("total",0)
        pc= int(pg/tot*100) if tot>0 else 0
        if ty=="FILE_UPLOAD":
            fn= os.path.basename(t["filepath"])
            line= f"UPLOAD: {fn} => {pg}/{tot} {pc}% {st}"
        elif ty=="DOWNLOAD":
            fid= t["file_id"]
            line= f"DOWNLOAD: ID={fid} => {pg}/{tot} {pc}% {st}"
        else:
            continue
        row+=1
        safe_addstr(stdscr,row,6,line,curses.color_pair(2))

def upload_file_menu(stdscr,cfg):
    curses.curs_set(1)
    fp= ask_input(stdscr,"Upload","Enter file path:","",curses.color_pair(1))
    if not fp: return
    if not os.path.isfile(fp):
        error_popup(stdscr,"File does not exist","Error",1)
        return
    dd= ask_input(stdscr,"Directory","Enter directory path:","root",curses.color_pair(1))
    if not dd.strip(): dd="root"
    sp= [x for x in dd.split("/") if x.strip()]
    if not sp or sp[0].lower()!="root": sp=["root"]+sp
    queue_upload(cfg,fp,sp)

def queue_upload(cfg,fp,pl):
    if not os.path.isfile(fp): return
    sz= os.path.getsize(fp)
    cc=1
    if sz>0:
        cc= max(1, math.ceil(sz/(cfg["chunk_size_mb"]*1024*1024)))
    fid= generate_fid()
    fn= os.path.basename(fp)
    root= cfg["directories"]
    c= root
    for nm in pl[1:]:
        sb= find_subdir(c,nm)
        if not sb:
            sb={"name":nm,"files":[],"subdirs":[],"expanded":False}
            c["subdirs"].append(sb)
        c= sb
    fobj={
        "file_id": fid,
        "file_name": fn,
        "chunk_count": cc,
        "upload_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "in_process":True
    }
    c["files"].append(fobj)
    save_config(cfg)
    tid="filetask_"+str(random.randint(10000,99999))
    with active_tasks_lock:
        active_tasks[tid]={
            "type":"file_upload",
            "filepath": fp,
            "file_id": fid,
            "progress":0,
            "total":cc,
            "status":"Uploading...",
            "finished":False
        }
    tokens= cfg["bot_tokens"]
    if not tokens:tokens=["NO_TOKENS"]
    i=0
    chunk_sz= cfg["chunk_size_mb"]*1024*1024
    def chunker():
        with open(fp,"rb")as f:
            idx=0
            while True:
                dd= f.read(chunk_sz)
                if not dd:break
                yield(idx,dd)
                idx+=1
    for idx,data in chunker():
        cid= "chunk_"+str(random.randint(10000,99999))
        tk= tokens[i%len(tokens)]
        i+=1
        with active_tasks_lock:
            active_tasks[cid]={
                "type":"chunk_upload",
                "file_id": fid,
                "chunk_idx": idx,
                "chunk_data": data,
                "part_filename": f"{fn}.part{idx}",
                "file_task_id": tid,
                "status":"pending",
                "finished":False,
                "token": tk
            }
        tasks_queue.put((cid,active_tasks[cid]))

def move_file_prompt(stdscr,cfg,fid):
    dd= ask_input(stdscr,"Move File","Enter new directory path:","root",curses.color_pair(1))
    if not dd.strip():return
    sp=[x for x in dd.split("/") if x.strip()]
    if not sp or sp[0].lower()!="root": sp=["root"]+sp
    move_file_record(cfg,fid,sp)

def main_loop(stdscr,cfg):
    screen="main_menu"
    sel=0
    tree_sel=0
    tree_top=0
    set_sel=0
    while True:
        stdscr.clear()
        do_banner(stdscr)
        if screen=="main_menu":
            items=["Browse Files","Upload File","Settings","Quit"]
            base=11
            for i,v in enumerate(items):
                arrow=">" if i==sel else" "
                if i==sel:
                    safe_addstr(stdscr,base+i,4,f"{arrow} {v}",curses.color_pair(2))
                else:
                    safe_addstr(stdscr,base+i,4,f"  {v}")
            used= base+ len(items)
            show_active_tasks(stdscr,used)
            stdscr.refresh()
            c= stdscr.getch()
            if c==-1: pass
            elif c in[ord('r'),ord('R')]:
                pass
            elif c== curses.KEY_UP:
                sel=(sel-1)% len(items)
            elif c== curses.KEY_DOWN:
                sel=(sel+1)% len(items)
            elif c in[10,13]:
                choice= items[sel]
                if choice=="Browse Files": screen="tree"; tree_sel=0; tree_top=0
                elif choice=="Upload File": upload_file_menu(stdscr,cfg)
                elif choice=="Settings": screen="settings"; set_sel=0
                elif choice=="Quit": return
        elif screen=="tree":
            arr= build_tree_lines(cfg["directories"],"",True)
            note="(Up/Down, PgUp/PgDn, Left/Right, Enter=Download, D=rmFile, X=rmDir, M=moveFile, ESC=menu)"
            safe_addstr(stdscr,10,max(0,(stdscr.getmaxyx()[1]-len(note))//2),note,curses.color_pair(2))
            used= draw_tree(stdscr,arr,tree_sel,tree_top)
            show_active_tasks(stdscr,used)
            stdscr.refresh()
            c= stdscr.getch()
            if c==-1: pass
            elif c in[ord('r'),ord('R')]:
                pass
            elif c== curses.KEY_UP:
                if arr: tree_sel=(tree_sel-1)% len(arr)
                if tree_sel<tree_top: tree_top=tree_sel
            elif c== curses.KEY_DOWN:
                if arr: tree_sel=(tree_sel+1)% len(arr)
                capacity= stdscr.getmaxyx()[0]-12
                if tree_sel>=tree_top+ capacity: tree_top= tree_sel-capacity+1
            elif c== curses.KEY_PPAGE:
                tree_top-=5
                if tree_top<0:tree_top=0
            elif c== curses.KEY_NPAGE:
                tree_top+=5
                if tree_top> len(arr)-1: tree_top=len(arr)-1
            elif c== curses.KEY_LEFT:
                if arr:
                    ln,ty,node= arr[tree_sel]
                    if ty=="dir" and node.get("expanded",False):
                        node["expanded"]=False
            elif c== curses.KEY_RIGHT:
                if arr:
                    ln,ty,node= arr[tree_sel]
                    if ty=="dir" and not node.get("expanded",False):
                        node["expanded"]=True
            elif c in[10,13]:
                if arr:
                    ln,ty,node= arr[tree_sel]
                    if ty=="file": queue_download(cfg,node["file_id"])
            elif c in[ord('d'),ord('D')]:
                if arr:
                    ln,ty,node= arr[tree_sel]
                    if ty=="file":
                        if confirm_delete_file(stdscr,node["file_name"]):
                            remove_file_record(cfg,node["file_id"])
            elif c in[ord('x'),ord('X')]:
                if arr:
                    ln,ty,node= arr[tree_sel]
                    if ty=="dir":
                        if node["name"]!="root":
                            if confirm_delete_dir(stdscr,node["name"]):
                                delete_dir(cfg,node["name"])
            elif c in[ord('m'),ord('M')]:
                if arr:
                    ln,ty,node= arr[tree_sel]
                    if ty=="file":
                        move_file_prompt(stdscr,cfg,node["file_id"])
            elif c==27:
                screen="main_menu"
        elif screen=="settings":
            items=["Set Server ID","Set Channel ID","Add Bot Token","Remove Bot Token","List Bot Tokens","Set Chunk Size","Back"]
            s1= f"Server ID  : {cfg['server_id']}"
            s2= f"Channel ID : {cfg['channel_id']}"
            s3= f"Bot Tokens : {len(cfg['bot_tokens'])}"
            s4= f"Chunk Size : {cfg.get('chunk_size_mb',5)} MB"
            safe_addstr(stdscr,11,4,s1,curses.color_pair(6))
            safe_addstr(stdscr,12,4,s2,curses.color_pair(6))
            safe_addstr(stdscr,13,4,s3,curses.color_pair(6))
            safe_addstr(stdscr,14,4,s4,curses.color_pair(6))
            base=16
            for i,v in enumerate(items):
                arrow=">" if i==set_sel else" "
                if i==set_sel:
                    safe_addstr(stdscr,base+i,4,f"{arrow} {v}",curses.color_pair(2))
                else:
                    safe_addstr(stdscr,base+i,4,f"  {v}")
            used= base+ len(items)
            show_active_tasks(stdscr,used)
            stdscr.refresh()
            c= stdscr.getch()
            if c==-1: pass
            elif c in[ord('r'),ord('R')]:
                pass
            elif c== curses.KEY_UP:
                set_sel=(set_sel-1)% len(items)
            elif c== curses.KEY_DOWN:
                set_sel=(set_sel+1)% len(items)
            elif c in[10,13]:
                choice= items[set_sel]
                if choice=="Set Server ID":
                    newv= ask_input(stdscr,"Server ID","Enter new:",cfg["server_id"],curses.color_pair(1))
                    if newv:
                        cfg["server_id"]= newv
                        save_config(cfg)
                elif choice=="Set Channel ID":
                    newc= ask_input(stdscr,"Channel ID","Enter new:",cfg["channel_id"],curses.color_pair(1))
                    if newc:
                        cfg["channel_id"]= newc
                        save_config(cfg)
                elif choice=="Add Bot Token":
                    tok= ask_input(stdscr,"Add Bot Token","Token:","",curses.color_pair(1))
                    if tok:
                        cfg["bot_tokens"].append(tok)
                        save_config(cfg)
                elif choice=="Remove Bot Token":
                    if not cfg["bot_tokens"]:
                        error_popup(stdscr,"No tokens","Notice",1)
                    else:
                        ix= ask_input(stdscr,"Remove Bot","Index?","",curses.color_pair(1))
                        try:
                            idx=int(ix)
                            if 0<= idx< len(cfg["bot_tokens"]):
                                cfg["bot_tokens"].pop(idx)
                                save_config(cfg)
                        except:pass
                elif choice=="List Bot Tokens":
                    if not cfg["bot_tokens"]:
                        error_popup(stdscr,"No tokens","Notice",1)
                    else:
                        lines=[]
                        with token_validity_lock:
                            for i,tok in enumerate(cfg["bot_tokens"]):
                                ok= token_validity_map.get(tok,False)
                                if ok: lines.append(f"[{i}] (OK)  {tok}")
                                else: lines.append(f"[{i}] (BAD) {tok}")
                        big="\n".join(lines)
                        big_list_popup(stdscr,big,"Bot Tokens")
                elif choice=="Set Chunk Size":
                    newv= ask_input(stdscr,"Chunk Size","Enter number between 5 and 25:","",curses.color_pair(1))
                    set_chunk_size(cfg,newv)
                elif choice=="Back":
                    screen="main_menu"
        time.sleep(0.1)

def big_list_popup(stdscr,msg,title):
    curses.curs_set(0)
    lines= msg.split("\n")
    bw=100
    bh= len(lines)+6
    h,w= stdscr.getmaxyx()
    if bh>h-4:bh=h-4
    if bw>w-4:bw=w-4
    y=(h-bh)//2
    x=(w-bw)//2
    wn= curses.newwin(bh,bw,y,x)
    wn.box()
    t_str=f" {title} "
    pos=(bw-len(t_str))//2
    safe_addstr(wn,0,pos,t_str,curses.color_pair(2))
    row=2
    for ln in lines:
        safe_addstr(wn,row,2,ln,curses.color_pair(2))
        row+=1
        if row>=bh-1: break
    wn.refresh()
    wn.getch()

def worker_loop(cfg):
    while not STOP_WORKERS:
        try:
            tid,task= tasks_queue.get(timeout=0.05)
        except: continue
        if task["type"]=="chunk_upload":
            do_chunk_upload(tid,task,cfg)
        elif task["type"]=="download":
            do_download(tid,task,cfg)
        with active_tasks_lock:
            task["finished"]= True
        tasks_queue.task_done()

def main(stdscr):
    curses.use_default_colors()
    curses.start_color()
    curses.resize_term(40,120)
    curses.init_pair(1,curses.COLOR_RED,-1)
    curses.init_pair(2,curses.COLOR_GREEN,-1)
    curses.init_pair(3,curses.COLOR_BLUE,-1)
    curses.init_pair(4,curses.COLOR_YELLOW,-1)
    curses.init_pair(5,curses.COLOR_CYAN,-1)
    curses.init_pair(6,curses.COLOR_WHITE,-1)
    stdscr.nodelay(False)
    cfg= load_config()
    apply_chunk_size(cfg)
    ok,e= valid_ids(cfg["server_id"],cfg["channel_id"])
    if not ok and e:
        error_popup(stdscr,"Warning: "+e,"Startup",1)
    tv= threading.Thread(target=background_token_verifier,args=(cfg,),daemon=True)
    tv.start()
    wcount=10
    if len(cfg["bot_tokens"])<10:
        wcount= len(cfg["bot_tokens"]) if cfg["bot_tokens"] else 1
    for _ in range(wcount):
        th= threading.Thread(target=worker_loop,args=(cfg,),daemon=True)
        th.start()
        worker_threads.append(th)
    main_loop(stdscr,cfg)
    global STOP_WORKERS
    STOP_WORKERS= True
    for th in worker_threads:
        th.join(timeout=1)
    with active_tasks_lock:
        for k,v in list(active_tasks.items()):
            if v.get("type")=="file_upload"and not v.get("finished",False):
                remove_file_record(cfg,v["file_id"])

if __name__=="__main__":
    curses.wrapper(main)
