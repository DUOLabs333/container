#!/usr/bin/env python
import subprocess
import sys
import os
import ast
import json
import signal
import shutil
import random, string

from .docker import *
from .misc import *

import utils

utils.GLOBALS=globals()

SHELL_CWD=os.environ.get("PWD")

def convert_colon_string_to_directory(string):
    string=utils.split_string_by_char(string,char=":")
    if string[0]=="root":
        string=string[1] #The directory is just the absolute path in the host
    elif len(string)==1:
        string=string[0] # No container was specified, so assume "root"
    else:
        string=f"{utils.ROOT}/{string[0]}/diff{string[1]}" # Container was specified, so use it
    string=os.path.expanduser(string)
    return string
utils.get_all_items=get_all_items

def generate_random_string(N):
    return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(N)) 

def is_jsonable(x):
    try:
        json.dumps(x)
        return True
    except (TypeError, OverflowError):
        return False
          
class Container:
    def __init__(self,_name,_flags={},_unionopts=None,_workdir='/',_env=None,_uid=None,_gid=None,_shell=None):
        if 'temp' in _flags:
            _name=generate_random_string(16) #Generate string for temp containers
        
        self.original_name=_name #For use in Init
        if ":" in _name or 'pull' in _flags: #Is a container
            _name='/'.join(parse_uri(_name))
        
        self.Class = utils.Class(self,_name,_flags,_workdir)
        self.unionopts=utils.get_value(_unionopts,[])
        
        self.env=utils.get_value(_env,["PATH=/bin:/usr/sbin:/sbin:/usr/bin","HOME=$(eval echo ~$(whoami))"])
        
        #Whether we mounted dev, proc, etc.
        self.mounted_special=False
        
        self.namespaces={"user":str2bool(os.getenv("CONTAINER_USER_NAMESPACES","0")),"net":str2bool(os.getenv("CONTAINER_NET_NAMESPACES","0"))}
        
        self.workdir=_workdir
        
        
        self.uid=utils.get_value(_uid,0 if self.namespaces['user'] else os.getuid())
        self.gid=utils.get_value(_gid,0 if self.namespaces['user'] else os.getgid())
        
        self.shell=_shell
        
        self.temp_layers=[]
        
        self.hardlinks=[]
        
        self.build=False
        
        self.base=False
        self.ports=[]
    
        self.setup=False #Whether _setup was run once
        
                    
        self._load() #Read from lock to initialize the same state
        
        
            
        
        
    #Functions        
    def _update(self,keys=None):
        if self.build:
            return #No lock file when building --- no need for it
        if isinstance(keys,str):
            keys=[keys]
        
        if not keys: #Just json it all
            keys=[attr for attr in dir(self) if not callable(getattr(self, attr)) and not attr.startswith("__") and is_jsonable(getattr(self,attr)) and attr not in ['flags']]
            
        with open(self.lock,"r") as f:
            data=json.load(f)
            
        for key in keys:
            data[key]=getattr(self,key)
        
        with open(self.lock,"w+") as f:
            json.dump(data,f)
             
    def _exit(self,a,b):
        
        self.Class.kill_auxiliary_processes()
        
        #Unmount dev,proc, etc. if directory exists
        if os.path.isdir("merged"):
            for dir in os.listdir("merged"):
                if os.path.ismount(f"merged/{dir}"):
                    if sys.platform=='linux':
                        utils.shell_command(["sudo","mount","--make-rslave",f"merged/{dir}"])
                        utils.shell_command(["sudo","umount","-R","-l",f"merged/{dir}"])
                    elif sys.platform=='darwin':
                        utils.shell_command(["sudo","umount",f"merged/{dir}"])
                    elif sys.platform=='cygwin':
                        utils.shell_command(["umount",f"merged/{dir}"])
                    
        
        diff_directories=[utils.split_string_by_char(_," ")[2] for _ in utils.shell_command(["mount"]).splitlines() if f"{utils.ROOT}/{self.name}/diff" in _]
        for dir in diff_directories:
             utils.shell_command(["umount","-l",dir])
             #utils.shell_command(["rm","-rf",dir])
        utils.shell_command((['sudo'] if self.namespaces["user"] else [])+["umount","-l","merged"])
    
        
        for hardlink in self.hardlinks:
            os.remove(hardlink) #Remove volume hardlinks when done
        
        utils.shell_command(["sudo","unlink","diff/etc/resolv.conf"])
        
        if self.namespaces['net']:
            utils.shell_command(["sudo","ip","netns","del",self.netns])
        
        for port in self.ports:
            for proto in ["TCP","UDP"]:
                for pid in list(map(int,[_ for _ in utils.shell_command(["lsof","-t","-i",f"{proto}@{port[0]}:{port[1]}"]).splitlines()])):
                    utils.kill_process_gracefully(pid) #Kill socat(s)

        exit()
    
    def _load(self):
        if os.path.isfile(self.lock):
            with open(self.lock,"r") as f:
                data=json.load(f)
            
            for key in data:
                setattr(self,key,data[key]) #Read variables from .lock and populate self with them as a bootstrap
    
    def _setup(self):
        if self.setup:
            return
        if not isinstance(self.unionopts,str): #If unionopts has not yet been joined, join it
            self.unionopts.insert(0,[self.name,"RW"]) #Make the current diff folder the top-most writable layer
                  
            temp=[]
            for _ in self.unionopts:
                temp.append(f"{utils.ROOT}/{_[0]}/diff={_[1]}")
            self.unionopts=":".join(temp)
            
        #Prevent merged from being mounted multiple times
        if not os.path.ismount("merged"):
            utils.shell_command((['sudo'] if self.namespaces["user"] else [])+["unionfs","-o","allow_other,cow,hide_meta_files",self.unionopts,"merged"])
        if not self.shell: #Only set if it doesn't exist yet
            for shell in ["bash","ash","sh"]:
                if os.path.islink(f"merged/bin/{shell}") or os.path.isfile(f"merged/bin/{shell}"): #Handle broken symlinks
                    self.Shell(f"/bin/{shell}")
                    break
        #Mount dev,proc, etc. over the unionfs to deal with mmap bugs (fuse may be patched to deal with this natively so I can just mount on the diff directory, but for now, this is what is needed)
        if not self.mounted_special:
            for dir in ["dev","proc"]:
                if not os.path.ismount(f"merged/{dir}"):
                    #Use bind mounts for special mounts, as bindfs has too many quirks (and I'm using sudo regardless)
                    if sys.platform=="darwin":
                        #MacOS doesn't have bind-mounts
                        fstype=utils.shell_command(["stat","-f","-c","%T",f"/{dir}"],stderr=subprocess.DEVNULL)
                        utils.shell_command(["sudo", "mount", "-t", fstype, fstype, f"merged/{dir}"])
                    elif sys.platform=="cygwin":
                        #Cygwin doesn't have rbind
                        utils.shell_command(["mount","-o","bind",f"/{dir}",f"merged/{dir}"])
                    elif sys.platform=="linux":
                        utils.shell_command(["sudo","mount","--rbind","--make-rprivate",f"/{dir}",f"merged/{dir}"])
                   
            self.mounted_special=True
            
        if self.namespaces['net']: #Start network namespace
            self.veth_pair={"netns":{"name":f"{self.normalized_name}-veth0"},"host":{"name":f"{self.normalized_name}-veth1"}}
            
            while True:
                cidr=[random.randint(0,255),random.randint(0,255)]
                if any(f"{cidr[0]}.{cidr[1]}.0.1/24" in _ for _ in utils.shell_command(["ip","addr"],stderr=subprocess.DEVNULL).splitlines()): #Check if CIDR range is already taken
                    continue
                else:
                    self.veth_pair['host']['cidr']=f"{cidr[0]}.{cidr[1]}.0.1/24"
                    self.veth_pair['netns']['cidr']=f"{cidr[0]}.{cidr[1]}.0.2/24"
                    break
            
            internet_interface=utils.shell_command("ip route get 8.8.8.8 | grep -Po '(?<=(dev ))(\S+)'",stderr=subprocess.DEVNULL,arbitrary=True) 
            commands=[
                ['ip', 'netns', 'add', self.netns],
                ['ip', 'netns', 'exec', self.netns, 'ip', 'link', 'set', 'lo', 'up'],
                ['ip', 'link', 'add', self.veth_pair['host']['name'], 'type', 'veth', 'peer', 'name', self.veth_pair['netns']['name']],
                ['ip', 'link', 'set', self.veth_pair['netns']['name'], 'netns', self.netns],
                ['ip', 'addr', 'add', self.veth_pair['host']['cidr'], 'dev', self.veth_pair['host']['name']],
                ['ip', 'netns', 'exec', self.netns, 'ip', 'addr', 'add', self.veth_pair['netns']['cidr'], 'dev', self.veth_pair['netns']['name']],
                ['ip', 'link', 'set', self.veth_pair['host']['name'], 'up'],
                ['ip', 'netns', 'exec', self.netns, 'ip', 'link', 'set', self.veth_pair['netns']['name'], 'up'],
                ['sysctl', '-w', 'net.ipv4.ip_forward=1'],
                ['iptables', '-A', 'FORWARD', '-o', internet_interface, '-i', self.veth_pair['host']['name'], '-j', 'ACCEPT'],
                ['iptables', '-A', 'FORWARD', '-i', internet_interface, '-o', self.veth_pair['host']['name'], '-j', 'ACCEPT'],
                ['iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', self.veth_pair['netns']['cidr'], '-o', internet_interface, '-j', 'MASQUERADE'],
                ['ip', 'netns', 'exec', self.netns, 'ip', 'route', 'add', 'default', 'via', self.veth_pair['host']['cidr'][:-3]],
                ["ip", "netns", "exec", self.netns, "sysctl", "-w", "net.ipv4.ip_unprivileged_port_start=1"]
                ]
            
            #temp_f=open(self.log,"w")
            for command in commands:
                utils.shell_command(["sudo"]+command,stdout=subprocess.DEVNULL)
                #utils.shell_command(["echo"]+command,stdout=temp_f)
            #temp_f.close()
            os.makedirs("diff/etc",exist_ok=True)
            utils.shell_command(["sudo","ln","-f","/etc/resolv.conf","diff/etc/resolv.conf"])

            os.makedirs("diff/tmp",exist_ok=True)
            os.chmod('diff/tmp',0o0777)
        #Check whether you can map users and/or groups
        self.maps=[]
        username=os.environ['USER']
        uid=os.getuid()
        
        if self.namespaces['user']:
            for file in ['uid','gid']:
                with open(f"/etc/sub{file}") as f:
                    for line in f:
                        if any(line.startswith(prefix) for prefix in [username,uid]): #User has a block of UIDs it can use
                            if file=='uid':
                                self.maps.append('--map-users=auto')
                            if file=='gid':
                                self.maps.append('--map-groups=auto')
                            break #No need to continue looping
              
        self._update()
        self.setup=True
    def Ps(self,process=None):
        if process=="main" or ("main" in self.flags):
            return self.Class.get_main_process()
        elif process=="auxiliary" or ("auxiliary" in self.flags):
            if not os.path.isdir("merged"):
                return []
            processes=[_ for _ in utils.shell_command(["lsof","-t","-w","--","merged"]).splitlines()]
            return list(map(int,processes))
    
    def Mount(self,IN,OUT):
        IN=convert_colon_string_to_directory(IN)
        if os.path.isdir(IN):
            try:
                os.makedirs(f"diff{OUT}",exist_ok=True)
            except FileExistsError:
                os.remove(f"diff{OUT}")
                os.makedirs(f"diff{OUT}",exist_ok=True)
            if not os.path.ismount(f"diff{OUT}"):
                utils.shell_command(["bindfs",IN,f"diff{OUT}"]) #Only use bindfs 1.15.1
        else:
            os.makedirs(os.path.dirname(f"diff{OUT}"),exist_ok=True) #Make parent directory if it doesn't exist
            try:
                os.link(IN,f"diff{OUT}")
            except FileExistsError:
                os.remove(f"diff{OUT}")
                os.link(IN,f"diff{OUT}")
            self.hardlinks.append(f"diff{OUT}")
            
    def Copy(self,src,dest):
        #Relative directory
        if not dest.startswith("/"):
            dest=f"diff{self.workdir}/{dest}"
        
        #Absolute directory
        else:
            dest=f"diff{dest}"
        
        src=convert_colon_string_to_directory(src)
        #Relative directory
        if not src.startswith("/"):
            src=f"{SHELL_CWD}/{src}"
            
        #Remove trailing slashes, in order to prevent gotchas with cp
        if src.endswith("/"):
            src=src[:-1]
        if dest.endswith("/"):
            dest=dest[:-1]
                        
        cp_error=utils.shell_command(["cp","-a",f"{src}",f"{dest}"])
        if "cp: cannot create" in cp_error:
            #dest does not exist, so create its parent's folder
            os.makedirs(os.path.dirname(dest),exist_ok=True)
            utils.shell_command(["cp","-a",f"{src}",f"{dest}"])

    def Loop(self,*args, **kwargs):
        self.Class.loop(*args, **kwargs)
        #Run(f'(while true; do "{command}"; sleep {delay}; done)')
        
    def Wait(self,*args, **kwargs):
        utils.wait(*args, **kwargs)
    
    def Namespace(self,key,value):
        self.namespaces[key]=value
        
    def Layer(self,layer,mode="RO"):
        layer=self.__class__(layer).name #Support getting Docker names
        if self.build:
            if len(os.listdir(os.path.join(utils.ROOT,layer,"diff")))<2:
                if os.path.exists(os.path.join(utils.ROOT,layer,"Containerfile.py")): #Only Build if there is a Containerfile.py
                    #Build layer if it doesn't exist
                    self.__class__(layer).Build()
                    #utils.shell_command(["container","build",layer])
                    self.temp_layers.append(layer) #Layer wasn't needed before so we can delete it after
        load_dependencies(self,utils.ROOT,layer)
        if [layer,mode] not in self.unionopts:
            self.unionopts.insert(0,[layer,mode]) #Prevent multiple of the same layers
            
    def Base(self,base):
        if self.base:
            return #Prevent multiple bases
        self.base=True
        #Make Base a synonym for Layer
        return self.Layer(base)
        
    def Workdir(self,*args, **kwargs):
        self.Class.workdir(*args, **kwargs)
        os.makedirs(f"diff{self.workdir}",exist_ok=True)
        self._update("workdir")
    
    def Env(self,var):
        self.env.append(var)
        self._update("env")
    
    def User(self,user=""):
        if user=="":
            self.uid=os.getuid()
            self.gid=os.getgid()
        else:
            user=utils.split_string_by_char(user,char=":")
            if len(user)==1:
                user.append(user[0]) #Make group the same as user if it is not available
            if user[0].isnumeric():
                self.uid=user[0]
            else:
                self.uid=int(self.Run(f"id -u {user[0]}",pipe=True))
                #self.uid=pwd.getpwnam(user[0])[2]
            
            if user[1].isnumeric():
                self.gid=user[1]
            else:
                self.gid=int(self.Run(f"id -g {user[1]}",pipe=True))
                #self.gid=pwd.getpwnam(user[1])[2]
        self._update(["uid","gid"])
    
    def Shell(self,shell):
        self.shell=shell
        self._update("shell")        
    
    def Volume(self,name,path):
        name=utils.split_string_by_char(name,char=":")
        
        #Allow to use volumes from other containers
        if len(name)==1:
            name.insert(0,self.name)
        volume_path=f"{utils.ROOT}/{name[0]}/Volumes/{name[1]}"
        
        self.Mount(volume_path,path)
        
        
    def Port(self,_from,_to=None):
        if not _to:
            _to=_from
        
        _from=str(_from)
        _to=str(_to)
        
        if ":" not in _from:
            _from="127.0.0.1:"+_from
            
        if ":" not in _to:
            _to="127.0.0.1:"+_to
        
        _from=_from.split(":")
        _to=_to.split(":")
        
        if _from[0]=="":
            _from[0]=="0.0.0.0"
            
        if _to[0]=="":
            _to[0]=="0.0.0.0"
        
        if is_port_in_use(_to): #Port is in use, so leave
            return 
            
        if _to in self.ports:
            return
        
        if not self.namespaces['net']:
            if _from==_to:
                return #If the source and destination are the same, don't socat it, since it will take up the port.
        for proto in ["tcp","udp"]:
            if self.namespaces['net']:
                sock_name=os.path.join(self.temp,f"{proto}-{':'.join(_to)}.sock")
                utils.shell_command(["socat", f"{proto}-listen:{_to[1]},fork,reuseaddr,bind={_to[0]}", f"""exec:'sudo ip netns exec {self.netns} socat STDIO "{proto}-connect:{_from[0]}:{_from[1]}"',nofork"""], stdout=subprocess.DEVNULL,block=False)
                #utils.shell_command(["sudo","ip","netns","exec",self.netns,"socat",f"UNIX-LISTEN:{sock_name},fork",f"{proto}-connect:127.0.0.1:{_from}"], stdout=subprocess.DEVNULL,block=False)
                #utils.shell_command(["sudo","socat",f"{proto}-listen:{_to},fork,reuseaddr,bind=127.0.0.1",f"UNIX-CONNECT:{sock_name}"],stdout=subprocess.DEVNULL,block=False)
            else:
               utils.shell_command(["socat", f"{proto}-l:{_to[1]},fork,reuseaddr,bind={_to[0]}", f"{proto}:{_from[0]}:{_from[1]}"], stdout=subprocess.DEVNULL,block=False)
        self.ports.append(_to)
        
    def Run(self,command="",pipe=False):
        
        self._setup()
        if self.build:
            if command.strip()!="":
                print(f"Command: {command}")
        
        with open(self.log,"a+") as log_file:
            log_file.write(f"Command: {command}\n")
            log_file.flush()
            
            #Pipe output to variable
            if pipe:
                stdout=subprocess.PIPE
                stderr=subprocess.DEVNULL
            #Print output to file
            else:
                stdout=log_file
                stderr=subprocess.STDOUT
            
            return utils.shell_command(chroot_command(self,command),stdout=stdout,stderr=stderr,stdin=subprocess.DEVNULL)
    
            
    #Commands      
    def Start(self):
        if "Started" in self.Status():
            return f"Container {self.name} is already started"
        
        
        if os.fork()==0:
            if os.fork()==0: #Double fork
                #If child, run code, then exit 
                with open(self.log,"a+") as f:
                    pass
                #Open a lock file so I can find it with lsof later
                lock_file=open(self.lock,"w+")
                
                with open(self.lock,"w+") as f:
                    json.dump({},f)
                
                self._update(["env","workdir", "uid","gid","shell"])
                signal.signal(signal.SIGTERM,self._exit)
                
                docker_layers=[]
                docker_commands=[]
                
                self.normalized_name=generate_random_string(7)
                if shutil.which("ip"): #Otherwise, it wouldn't matter
                    while self.normalized_name+"-netns" in utils.shell_command(["ip","netns","list"]).splitlines():
                         self.normalized_name=generate_random_string(7)
                    self.netns=f"{self.normalized_name}-netns"
                if os.path.isfile("docker.json"):
                    docker_layers, docker_commands=CompileDockerJson("docker.json")
                
                #Set up layers first from docker_layer
                utils.execute(self,'\n'.join(docker_layers))
                #Run container-compose.py as an intermediary step
                utils.execute(self,open("container-compose.py"))
                
                utils.execute(self,'\n'.join(docker_commands))
                
                #Don't have to put Run() in container-compose.py just to start it
                self.Run()
                self.Wait()
                exit()
            exit()
        
    def Build(self):
        self.Stop()
        self.build=True
        self.namespaces['net']=False #Don't enable it when building, as it just gets messy
        signal.signal(signal.SIGTERM,self._exit)
        signal.signal(signal.SIGINT,self._exit)
        
        utils.execute(self,open("Containerfile.py"))

        self.Stop()
        remove_empty_folders_in_diff()
        for layer in self.temp_layers:
            #Clean layer if it was temporary
            self.__class__(layer).Clean()
            #utils.shell_command(["container","clean",layer])
        self._exit(1,2)
        
       
    def Stop(self):
        return [self.Class.stop()]

    def Restart(self):
        return self.Class.restart()
    
    def Chroot(self):

        if "Stopped" in self.Status():
            stopped=True
        else:
            stopped=False
              
        if stopped:
            self.Start()
            while not os.listdir("merged"): #Wait until merged directory has files before you attempt to chroot
                pass
#            while not self.setup:
#                try:
#                    self._load()
#                except ValueError:
#                    pass
            while not hasattr(self,"netns") or not self.shell:
                try:
                    self._load()
                except ValueError:
                    pass
        command=self.shell #By default, run the shell
        if "run" in self.flags:
            command=self.flags["run"]  
        utils.shell_command(chroot_command(self,command),stdout=None)
        if stopped:
            self.Stop()
        
        if "and-stop" in self.flags:
            return [self.Stop()]
    
    def Prune(self):
        root=os.path.join(utils.ROOT,self.name)
        containers=get_all_items(root)
        layers={'container':[],"folder":[]}
        for _ in containers:
            try:
                with open(os.path.join(root,_,"docker.json")) as f:
                    layers['container'].extend(json.load(f)["layers"])
            except FileNotFoundError: #If docker.json doesn't exist for any container, root is not a Docker registry, and can be safely ignored
                return
                
        for _ in os.listdir(root):
            if os.listdir(os.path.join(root,_))==['diff']: #All non-container layers
                layers['folder'].append(os.path.join(self.name,_))
        
        if layers['folder']==[]: #If there are no layers, then root is not a registry any can be safely ignored
            return
        difference=[_ for _ in layers['folder'] if _ not in layers['container']] #Get unused layers
        print(difference)
        for _ in difference:
            self.__class__(_).Delete()
            
    def List(self):
        return self.Class.list()

    def Init(self):
        
        if "pull" in self.flags:
            
            Import(self.original_name,utils.ROOT)
            
            if "dockerfile" in self.flags:
                Convert(self.flags["dockerfile"],os.path.join(utils.ROOT,self.name))
        os.makedirs(f"{utils.ROOT}/{self.name}",exist_ok=True)
        os.chdir(f"{utils.ROOT}/{self.name}")
        os.makedirs("diff",exist_ok=True)
        os.makedirs("merged",exist_ok=True)
        
        if 'temp' in self.flags:
            self.flags['no-edit']=''
            self.flags['only-chroot']=''
        with open(f"container-compose.py",'a'):
            pass
        
        if 'build' in self.flags:
            with open(f"Containerfile",'a'):
                pass
        
        if 'no-edit' not in self.flags:
            self.Edit()
            
        if utils.check_if_element_any_is_in_list(['only-chroot','and-chroot'],self.flags):
            return [self.Start(),self.Delete() if 'temp' in self.flags else None]

    def Edit(self):
        if 'build' in self.flags:
            utils.shell_command([os.getenv("EDITOR","vi"),f"{utils.ROOT}/{self.name}/Containerfile.py"],stdout=None)
        else:
            utils.shell_command([os.getenv("EDITOR","vi"),f"{utils.ROOT}/{self.name}/container-compose.py"],stdout=None)

    def Status(self):
        return self.Class.status()

    def Log(self):
        self.Class.log()
    
    def Clean(self):
        self.Stop()
        os.system(f"sudo rm -rf diff/*")
    
    def Delete(self):
        self.Stop()
        utils.shell_command(["sudo","rm","-rf",f"{utils.ROOT}/{self.name}"])
        
        if 'auto-pune-experimental' in self.flags:
            if 'no-prune' not in self.flags:
                self.__class__(self.name.split('/')[0]).Prune()
                
    def Watch(self):
        self.Class.watch()

utils.CLASS=Container

utils.ROOT=utils.get_root_directory()  
   
def main():
    NAMES,FLAGS,FUNCTION=utils.extract_arguments()
    for name in utils.list_items_in_root(NAMES, FLAGS):
        item=utils.CLASS(name,FLAGS)
        result=utils.execute_class_method(item,FUNCTION)
        
        utils.print_list(result)
        

    
