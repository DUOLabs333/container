#!/usr/bin/env python
import subprocess
import re
import sys
import os
import threading
import time
import ast
#import pwd, grp
import json
import hashlib
import signal
import shutil
import random
import types
import getpass

# < include '../utils/utils.py' >
import utils

# < include 'modules/_utils.py' >
import _utils

utils.GLOBALS=globals()

SHELL_CWD=os.environ.get("PWD")

#Helper functions  
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
    

def remove_empty_folders_in_diff():
    walk = list(os.walk("diff"))
    for path, _, _ in walk[::-1]:
        if not path.startswith("diff/.unionfs"):
            if len(os.listdir(path)) == 0:
                os.rmdir(path)
                
def str2bool(v):
  return v.lower() in ("yes", "true", "t", "1")
  
class Container:
    def __init__(self,_name,_flags=None,_unionopts=None,_workdir='/',_env=None,_uid=None,_gid=None,_shell=None):
        if 'temp' in _flags:
            _name=''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(16)) #Generate string for temp containers
        _name=_name.replace('/','_') #To allow for Docker-imported containers to work
        self.Class = utils.Class(self)
        self.Class.class_init(_name,_flags,_workdir)
        
        self.unionopts=utils.get_value(_unionopts,[])
        
        self.env=utils.get_value(_env,f"export PATH=/bin:/usr/sbin:/sbin:/usr/bin HOME=$(eval echo ~$(whoami))")
        
        #Whether we mounted dev, proc, etc.
        self.mounted_special=False
        
        self.namespaces=types.SimpleNamespace(user=str2bool(os.getenv("CONTAINER_USER_NAMESPACES","0")),net=str2bool(os.getenv("CONTAINER_NET_NAMESPACES","0")))
        
        self.workdir=_workdir
        
        
        self.uid=utils.get_value(_uid,0 if self.namespaces.user else os.getuid())
        self.gid=utils.get_value(_gid,0 if self.namespaces.user else os.getgid())
        
        self.shell=utils.get_value(_shell,"/bin/sh")
        
        self.temp_layers=[]
        
        self.hardlinks=[]
        
        self.build=False
        
        self.ports=[]
        if self.namespaces.net:
            self.netns=f"{self.name}-netns"
            self.veth_pair=types.SimpleNamespace(netns=types.SimpleNamespace(name=f"{self.name}-veth0"),host=types.SimpleNamespace(name=f"{self.name}-veth1"))
            
            while True:
                cidr=[random.randint(0,255),random.randint(0,255)]
                if any(f"{cidr[0]}.{cidr[1]}.0.1/24" in _ for _ in utils.shell_command(["ip","addr"],stderr=subprocess.DEVNULL)): #Check if CIDR range is already taken
                    continue
                else:
                    self.veth_pair.host.cidr=f"{cidr[0]}.{cidr[1]}.0.1/24"
                    self.veth_pair.netns.cidr=f"{cidr[0]}.{cidr[1]}.0.2/24"
                    break
            
        
        
    #Functions
    def Run(self,command="",pipe=False):
        if self.build:
            if command.strip()!="":
                print(f"Command: {command}")
        
        if not isinstance(self.unionopts,str): #If unionopts has not yet been joined, join it
            self.unionopts.insert(0,[self.name,"RW"]) #Make the current diff folder the top-most writable layer
                  
            temp=[]
            for _ in self.unionopts:
                temp.append(f"{utils.ROOT}/{_[0]}/diff={_[1]}")
            self.unionopts=":".join(temp)
            
        #Prevent merged from being mounted multiple times
        if not os.path.ismount("merged"):
            utils.shell_command(["unionfs","-o","allow_other,cow,hide_meta_files",self.unionopts,"merged"])
               
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
                        utils.shell_command(["sudo","mount","--rbind",f"/{dir}",f"merged/{dir}"])
                   
            self.mounted_special=True
            
        with open(f"{utils.TEMPDIR}/container_{self.name}.log","a+") as log_file:
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
            
            return utils.shell_command(_utils.misc.chroot_command(self,command),stdout=stdout,stderr=stderr)
            
    
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
        
    def Base(self,base):
        #Make Base a synonym for Layer
        return self.Layer(base)
        
    def Wait(self,*args, **kwargs):
        utils.wait(*args, **kwargs)

    def Layer(self,layer,mode="RO"):
        if self.build:
            if len(os.listdir(f"{utils.ROOT}/{layer}/diff"))<2:
                #Build layer if it doesn't exist
                self.__class__(layer).Build()
                #utils.shell_command(["container","build",layer])
                self.temp_layers.append(layer) #Layer wasn't needed before so we can delete it after
        _utils.misc.load_dependencies(self,layer)
        self.unionopts.insert(0,[layer,mode])
    
    def Workdir(self,*args, **kwargs):
        self.Class.workdir(*args, **kwargs)
        os.makedirs(f"diff{self.workdir}",exist_ok=True)
        self.Update("workdir")
    
    def Env(self,*args, **kwargs):
        self.env=utils.add_environment_variable_to_string(self.env,*args, **kwargs)
        self.Update("env")
    
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
        self.Update(["uid","gid"])
    
    def Shell(self,shell):
        self.shell=shell
        self.Update("shell")        
    
    def Volume(self,name,path):
        name=utils.split_string_by_char(name,char=":")
        
        #Allow to use volumes from other containers
        if len(name)==1:
            name.insert(0,self.name)
        volume_path=f"{utils.ROOT}/{name[0]}/Volumes/{name[1]}"
        
        self.Mount(volume_path,path)
        
    def Update(self,keys):
        if self.build:
            return #No lock file when building --- no need for it
        if isinstance(keys,str):
            keys=[keys]
        
        with open(f"{utils.TEMPDIR}/container_{self.name}.lock","r") as f:
            data=json.load(f)
            
        for key in keys:
            data[key]=getattr(self,key)
        
        with open(f"{utils.TEMPDIR}/container_{self.name}.lock","w+") as f:
            json.dump(data,f)
             
    def Exit(self,a,b):
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
             utils.shell_command(["rm","-rf",dir])
        utils.shell_command(["umount","-l","merged"])
    
        
        for hardlink in self.hardlinks:
            os.remove(hardlink) #Remove volume hardlinks when done
        
        utils.shell_command(["sudo","unlink","diff/etc/resolv.conf"])
        
        if self.namespaces.net:
            utils.shell_command(["sudo","ip","netns","del",self.netns])
        
        for port in self.ports:
            for pid in list(map(int,[_ for _ in utils.shell_command(["lsof","-t","-i",f":{port}"]).splitlines()])):
                utils.kill_process_gracefully(pid) #Kill socat(s)

        exit()
        
    def Port(self,_from,_to):
        _from=int(_from)
        _to=int(_to)
        if not self.namespaces.net:
            if _from==_to:
                return #If the ports are the same, don't socat it, since it will take up the port.
        for proto in ["tcp","udp"]:
            if self.namespaces.net:
               utils.shell_command(["socat", f"{proto}-listen:{_to},fork,reuseaddr,bind=127.0.0.1", f"""exec:'sudo ip netns exec {self.netns} socat STDIO "{proto}-connect:127.0.0.1:{_from}"',nofork"""], stdout=subprocess.DEVNULL,block=False)
            else:
               utils.shell_command(["socat", f"{proto}-l:{_to},fork,reuseaddr,bind=127.0.0.1", f"{proto}:127.0.0.1:{_from}"], stdout=subprocess.DEVNULL,block=False)
        self.ports.append(_to)
            
    #Commands      
    def Start(self):
        if "Started" in self.Status():
            return f"Container {self.name} is already started"
        
        #Fork process, so it can run in the background
        pid=os.fork()
        
        #If child, run code, then exit 
        if pid==0:
            with open(f"{utils.TEMPDIR}/container_{self.name}.log","a+") as f:
                pass
            #Open a lock file so I can find it with lsof later
            self.lock=open(f"{utils.TEMPDIR}/container_{self.name}.lock","w+")
            
            with open(f"{utils.TEMPDIR}/container_{self.name}.lock","w+") as f:
                json.dump({},f)
            
            self.Update(["env","workdir", "uid","gid","shell"])
            
            signal.signal(signal.SIGTERM,self.Exit)
            
            if self.namespaces.net: #Start network namespace
                internet_interface=utils.shell_command("ip route get 8.8.8.8 | grep -Po '(?<=(dev ))(\S+)'",stderr=subprocess.DEVNULL,arbitrary=True) 
                commands=[
                    ['ip', 'netns', 'add', self.netns],
                    ['ip', 'netns', 'exec', self.netns, 'ip', 'link', 'set', 'lo', 'up'],
                    ['ip', 'link', 'add', self.veth_pair.host.name, 'type', 'veth', 'peer', 'name', self.veth_pair.netns.name],
                    ['ip', 'link', 'set', self.veth_pair.netns.name, 'netns', self.netns],
                    ['ip', 'addr', 'add', self.veth_pair.host.cidr, 'dev', self.veth_pair.host.name],
                    ['ip', 'netns', 'exec', self.netns, 'ip', 'addr', 'add', self.veth_pair.netns.cidr, 'dev', self.veth_pair.netns.name],
                    ['ip', 'link', 'set', self.veth_pair.host.name, 'up'],
                    ['ip', 'netns', 'exec', self.netns, 'ip', 'link', 'set', self.veth_pair.netns.name, 'up'],
                    ['sysctl', '-w', 'net.ipv4.ip_forward=1'],
                    ['iptables', '-A', 'FORWARD', '-o', internet_interface, '-i', self.veth_pair.host.name, '-j', 'ACCEPT'],
                    ['iptables', '-A', 'FORWARD', '-i', internet_interface, '-o', self.veth_pair.host.name, '-j', 'ACCEPT'],
                    ['iptables', '-t', 'nat', '-A', 'POSTROUTING', '-s', self.veth_pair.netns.cidr, '-o', internet_interface, '-j', 'MASQUERADE'],
                    ['ip', 'netns', 'exec', self.netns, 'ip', 'route', 'add', 'default', 'via', self.veth_pair.host.cidr[:-3]],
                    ["ip", "netns", "exec", self.netns, "sysctl", "-w", "net.ipv4.ip_unprivileged_port_start=1"]
                    ]
                 
                for command in commands:
                    utils.shell_command(["sudo"]+command,stdout=subprocess.DEVNULL)
                
                if not os.path.isdir("diff/etc"):
                    os.makedirs("diff/etc",exist_ok=True)
                utils.shell_command(["sudo","ln","-f","/etc/resolv.conf","diff/etc/resolv.conf"])
            #Run container-compose.py
            with open(f"{utils.ROOT}/{self.name}/container-compose.py") as f:
                code=f.read()
            exec(code,self.globals,locals())
            
            #Don't have to put Run() in container-compose.py just to start it
            self.Run()
            self.Wait()
            exit()
        
    def Build(self):
        self.Stop()
        self.build=True
        self.namespaces.net=False #Don't enable it when building, as it just gets messy
        signal.signal(signal.SIGTERM,self.Exit)
        signal.signal(signal.SIGINT,self.Exit)
        with open("Containerfile.py") as f:
         code = compile(f.read(), 'Containerfile.py', 'exec')
         exec(code,self.globals,locals())
        self.Stop()
        remove_empty_folders_in_diff()
        for layer in self.temp_layers:
            #Clean layer if it was temporary
            self.__class__(layer).Clean()
            #utils.shell_command(["container","clean",layer])
        self.Exit(1,2)
        
       
    def Stop(self):
        return [self.Class.stop()]

    def Restart(self):
        return self.Class.restart()
    
    def Chroot(self):

        if "Stopped" in self.Status():
            stopped=True
        else:
            stopped=False
            with open(f"{utils.TEMPDIR}/container_{self.name}.lock","r") as f:
                data=json.load(f)
            
            for key in data:
                setattr(self,key,data[key]) #Read variables from .lock and populate self with them as a bootstrap
            
        command=self.shell #By default, run the shell
        if "run" in self.flags:
            command=self.flags["run"]
        if stopped:
            self.Start()
            while not os.listdir("merged"): #Wait until merged directory has files before you attempt to chroot
                pass
        utils.shell_command(_utils.misc.chroot_command(self,command),stdout=None)
        if stopped:
            self.Stop()
        
        if "and-stop" in self.flags:
            return [self.Stop()]
    
    
    def List(self):
        return self.Class.list()

    def Init(self):
       
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
    
    def Watch(self):
        self.Class.watch()

utils.CLASS=Container

utils.ROOT=utils.get_root_directory()  
   
if __name__ == "__main__":
    NAMES,FLAGS,FUNCTION=utils.extract_arguments()
    
    for name in utils.list_items_in_root(NAMES, FLAGS):
        if FUNCTION!="init": #If you're running Init, skip this check, as you know it doesn't exist yet.
            try:
                item=utils.CLASS(name,FLAGS)
            except utils.DoesNotExist:
                print(f"Container {name} does not exist")
                continue
        result=utils.execute_class_method(item,FUNCTION)
        
        utils.print_list(result)
        

    
