#!/usr/bin/env python
import subprocess
import sys
import os
import json
import shutil

from .docker import *
from .misc import *

import utils

class ParsingFinished(Exception):
    pass

def unionfs_command(opts,mountpoint):
    
    return ["unionfs","-o","allow_other,cow,hide_meta_files",":".join([opt[0]+"="+opt[1] for opt in opts]),mountpoint]

class Container(utils.Class):
    def __init__(self,name,flags,**kwargs):
    
        if 'temp' in flags:
            name=generate_random_string(16) #Generate string for temp containers
        
        self.original_name=name #For use in Init
        if ":" in name or 'pull' in flags: #Is a container
            name='/'.join(parse_uri(name))
        
        
        self.namespaces={"user":str2bool(os.getenv("CONTAINER_USER_NAMESPACES","0")),"net":str2bool(os.getenv("CONTAINER_NET_NAMESPACES","0"))}
        
        self.SHELL_CWD=os.environ.get("PWD")
        
        self.uid=kwargs.get("uid",0 if self.namespaces['user'] else os.getuid())
        self.gid=kwargs.get("gid",0 if self.namespaces['user'] else os.getgid())
        
        self.workdir=kwargs.get("workdir","/")
        self.shell=kwargs.get("shell",None)
        self.unionopts=kwargs.get("unionopts",[])
        self.env=kwargs.get("env",["PATH=/bin:/usr/sbin:/sbin:/usr/bin","HOME=$(eval echo ~$(whoami))"])
        self.build=kwargs.get("build",False)
        
        self.temp_layers=[]
        self.run_layers_commands=[]
        self.open_ports=[]
        self.ports={}
        self.hardlinks=[]
        
        self.base=False
        self.mounted_special=False #Whether we mounted dev, proc, etc.
                    
        super().__init__(name,flags,kwargs)
        
        
    def _exit(self):
        #Unmount dev,proc, etc. if directory exists
        if os.path.isdir("merged"):
            for dir in os.listdir("merged"):
                dir=os.path.join("merged",dir)
                if os.path.ismount(dir):
                    if sys.platform=='linux':
                        utils.shell_command(["sudo","mount","--make-rslave",dir])
                        utils.shell_command(["sudo","umount","-R","-l",dir])
                    elif sys.platform=='darwin':
                        utils.shell_command(["sudo","umount",dir])
                    elif sys.platform=='cygwin':
                        utils.shell_command(["umount",dir])
                    
        
        diff_directories=[utils.split_string_by_char(_," ")[2] for _ in utils.shell_command(["mount"]).splitlines() if os.path.join(self.directory,"diff") in _]
        for dir in diff_directories:
             utils.shell_command(["umount","-l",dir])
             #utils.shell_command(["rm","-rf",dir])
        utils.shell_command((['sudo'] if True else [])+["umount","-l","merged"])
    
        
        for hardlink in self.hardlinks:
            os.remove(hardlink) #Remove volume hardlinks when done
        
        utils.shell_command(["sudo","unlink",os.path.join("diff","etc","resolv.conf")])
        
        if self.namespaces['net']:
            utils.shell_command(["sudo","ip","netns","del",self.netns])
        
        for port in self.open_ports:
            for proto in ["TCP","UDP"]:
                command=["lsof","-t","-i",f"{proto}@{port[0]}:{port[1]}"]
                if proto=="TCP":
                    command.append(f"-s{proto}:LISTEN")
                for pid in list(map(int,[_ for _ in utils.shell_command(command).splitlines()])):
                    utils.kill_process_gracefully(pid) #Kill socat(s)
    
    def _setup(self):          
        self.unionopts.insert(0,[self.name,"RW"]) #Make the current diff folder the top-most writable layer
        
        for opt in self.unionopts:
            opt[0]=os.path.join(self.ROOT,utils.name_to_filename(opt[0]),"diff")
            
        #Prevent merged from being mounted multiple times
        if not os.path.ismount("merged"):
            utils.shell_command((['sudo'] if True else [])+unionfs_command(self.unionopts,"merged"))
            
        if not self.shell: #Only set if it doesn't exist yet
            for shell in ["bash","ash","sh"]:
                if os.path.islink(os.path.join("merged","bin",shell)) or os.path.isfile(os.path.join("merged","bin",shell)): #Handle broken symlinks
                    self.Shell(f"/bin/{shell}")
                    break
                    
        #Mount dev,proc, etc. over the unionfs to deal with mmap bugs (fuse may be patched to deal with this natively so I can just mount on the diff directory, but for now, this is what is needed)
        if not self.mounted_special:
            for dir in ["dev","proc"]:
                merged_dir=os.path.join("merged",dir)
                if not os.path.ismount( merged_dir):
                    #Use bind mounts for special mounts, as bindfs has too many quirks (and I'm using sudo regardless)
                    if sys.platform=="darwin":
                        #MacOS doesn't have bind-mounts
                        fstype=utils.shell_command(["stat","-f","-c","%T",f"/{dir}"],stderr=subprocess.DEVNULL)
                        utils.shell_command(["sudo", "mount", "-t", fstype, fstype,  merged_dir])
                    elif sys.platform=="cygwin":
                        #Cygwin doesn't have rbind
                        utils.shell_command(["mount","-o","bind",f"/{dir}", merged_dir])
                    elif sys.platform=="linux":
                        utils.shell_command(["sudo","mount","--rbind","--make-rprivate",f"/{dir}", merged_dir])
                   
            self.mounted_special=True
            
        if self.namespaces['net']: #Start network namespace
            netns_name=generate_random_string(7)
            if shutil.which("ip"): #Otherwise, it wouldn't matter
                while True:
                    netns_name=generate_random_string(7)
                    self.netns=f"{netns_name}-netns"
                    if self.netns not in utils.shell_command(["ip","netns","list"]).splitlines():
                        break
                
            self.veth_pair={"netns":{"name":f"{netns_name}-veth0"},"host":{"name":f"{netns_name}-veth1"}}
            
            while True:
                cidr=[random.randint(0,255),random.randint(0,255)]
                if any(f"{cidr[0]}.{cidr[1]}.0.1/24" in _ for _ in utils.shell_command(["ip","addr"],stderr=subprocess.DEVNULL).splitlines()): #Check if CIDR range is already taken
                    continue
                else:
                    self.veth_pair['host']['cidr']=f"{cidr[0]}.{cidr[1]}.0.1/24"
                    self.veth_pair['netns']['cidr']=f"{cidr[0]}.{cidr[1]}.0.2/24"
                    break
            
            internet_interface=utils.shell_command("ip route get 8.8.8.8 | grep -Po '(?<=(dev ))(\S+)'",stderr=subprocess.DEVNULL,shell=True).strip()
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
            for i,command in enumerate(commands):
                if i==1:
                    while not os.path.exists(f"/run/netns/{self.netns}"): #Wait until net namespace is up before running anything
                        pass
                utils.shell_command(["sudo"]+command,stdout=subprocess.DEVNULL)

        for _from, _to in self.ports.items():
            for proto in ["tcp","udp"]:
                if self.namespaces['net']:
                    sock_name=os.path.join(self.tempdir,f"{proto}-{':'.join(_to)}.sock")
                    utils.shell_command(["socat", f"{proto}-listen:{_to[1]},fork,reuseaddr,bind={_to[0]}", f"""exec:'sudo ip netns exec {self.netns} socat STDIO "{proto}-connect:{_from[0]}:{_from[1]}"',nofork"""], stdout=subprocess.DEVNULL,block=False)
                    #utils.shell_command(["sudo","ip","netns","exec",self.netns,"socat",f"UNIX-LISTEN:{sock_name},fork",f"{proto}-connect:127.0.0.1:{_from}"], stdout=subprocess.DEVNULL,block=False)
                    #utils.shell_command(["sudo","socat",f"{proto}-listen:{_to},fork,reuseaddr,bind=127.0.0.1",f"UNIX-CONNECT:{sock_name}"],stdout=subprocess.DEVNULL,block=False)
                else:
                   utils.shell_command(["socat", f"{proto}-l:{_to[1]},fork,reuseaddr,bind={_to[0]}", f"{proto}:{_from[0]}:{_from[1]}"], stdout=subprocess.DEVNULL,block=False)
            self.open_ports.append(_to)
            
        os.makedirs(os.path.join("diff","etc"),exist_ok=True)
        utils.shell_command(["sudo","ln","-f", os.path.join(os.sep,"etc","resolv.conf"),os.path.join("diff","etc","resolv.conf")])

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
              
        self.update_lockfile()
            
    def _get_config(self):
        config=[]
        
        docker_layers=[]
        docker_commands=[]
        if os.path.isfile("docker.json"):
            docker_layers, docker_commands=CompileDockerJson("docker.json")
            
        config.extend(docker_layers)
        if os.path.isfile("container-compose.py"):
            config.append(open("container-compose.py").read())
        config.extend(docker_commands)
        
        return config
        
    def get_auxiliary_processes(self):
        if not os.path.isdir("merged"):
            return []
        processes=[_ for _ in utils.shell_command((["sudo"] if not self.namespaces["user"] else [])+["lsof","-t","-w","--","merged"]).splitlines()]
        return list(map(int,processes))
    
    @classmethod
    def get_all_items(cls):
        return [utils.filename_to_name(_) for _ in get_all_items(cls._get_root())]
    
            
    def Mount(self,IN,OUT):
        IN=convert_colon_string_to_directory(self,IN)
        if os.path.isdir(IN):
            try:
                os.makedirs(f"diff{OUT}",exist_ok=True)
            except FileExistsError:
                os.remove(f"diff{OUT}")
                os.makedirs(f"diff{OUT}",exist_ok=True)
            if not os.path.ismount(f"diff{OUT}"):
                utils.shell_command(unionfs_command([[IN,"RW"]],f"diff{OUT}"))
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
            dest=os.path.join(f"diff{self.workdir}",dest)
        
        #Absolute directory
        else:
            dest=f"diff{dest}"
        
        src=convert_colon_string_to_directory(self,src)
        #Relative directory
        if not src.startswith("/"):
            src=os.path.join(self.SHELL_CWD,src)
            
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

 
    def Namespace(self,key,value):
        self.namespaces[key]=value
        
    def Layer(self,layer,mode="RO",run=False):
        
        if [layer,mode] in self.unionopts: #Prevent multiple of the same layer
            return
            
        if self.build:
            layer=self.__class__(layer,{},build=True)
            if len(os.listdir(os.path.join(self.ROOT,layer.name,"diff")))<2: #Nothing in diff, so we should build layer
                if os.path.exists(os.path.join(self.ROOT,layer.name,"Containerfile.py")): #Only Build if there is a Containerfile.py
                    layer.Build() #Maybe just make Build a link to Start, but just using Containerfile?
                    self.temp_layers.append(layer) #Layer wasn't needed before so we can delete it after
            layer=layer.name
        
        
        parsed_config=[]
        
        parsing_environment={}
        
        #parsing_environment["parsed_config"]=parsed_config
        
        def make_func(attr):
            def func(*args,**kwargs):
                if attr in ["Layer","Base","Env","Shell"]:
                    parsed_config.append([attr,args,kwargs])
                else:
                    if attr=="Run": #No need to do anything else
                        raise ParsingFinished
                    pass
            return func
        
        for attr in self.attributes:
            if not(attr[0].isupper() and callable(getattr(self,attr))):
                continue
            parsing_environment[attr]=make_func(attr)
        
        layer=self.__class__(layer,{})
        layer_config=layer._get_config()
        try:
            layer._exec(layer_config,parsing_environment)
        except ParsingFinished:
            pass
            
        for command in parsed_config:
            getattr(self,command[0])(*command[1],**command[2])
        
        if run: #Put the entire config in list, but only run what hasn't been parsed
            self.run_layers_commands.append(layer_config)
                     
        #Add method _parse that allows you to parse for specific functions?
                
        self.unionopts.insert(0,[layer.name,mode])
          
    def Base(self,base):
        if self.base:
            return #Prevent multiple bases
        self.base=True
        #Make Base a synonym for Layer
        return self.Layer(base)
        
    def Workdir(self,*args, **kwargs):
        super().Workdir(*args, **kwargs)
        os.makedirs(f"diff{self.workdir}",exist_ok=True)
    
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
    
    def Shell(self,shell):
        self.shell=shell
    
    def Volume(self,name,path):
        name=utils.split_string_by_char(name,char=":")
        
        #Allow to use volumes from other containers
        if len(name)==1:
            name.insert(0,self.name)
        volume_path=os.path.join(self.ROOT,utils.name_to_filename(name[0]),"Volumes",name[1])
        
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
            
        if _to in self.open_ports:
            return
        

        if not self.namespaces['net']:
            if _from==_to:
                return #If the source and destination are the same, don't socat it, since it will take up the port.
        self.ports[tuple(_from)]=_to
        
    def Run(self,command="",**kwargs):

        if self.config_finished:
            self.config_finished=False #Since there's more things to run
            run_layer_environment={}
            for command in ["Layer","Base","Env","Shell"]:
                run_layer_environment[command]=lambda *args, **kwargs : None
                
            for command in self.run_layers_commands:
                self._exec(command,run_layer_environment) #Runnable layer's commands should be run after all other commands
            self.run_layers_commands=[]
            self.config_finished=True
            return
        command_wrapper=lambda : chroot_command(self,command) #Delay execution until setup is complete so that self.maps can be defined

        return super().Run(command_wrapper,display_command=command,**kwargs)
    
    #Commands
        
    def command_Build(self):
        self.Stop()
        self=self.__class__(self.name,self.flags) #Reset
        self.fork=False #Build runs synchronously
        self.build=True
        self.namespaces['net']=False #Don't enable it when building, as it just gets messy
        
        self._get_config=lambda *args, **kwargs: [open("Containerfile.py").read()]
        
        self.Start()
        self.Stop()
        remove_empty_folders_in_diff() #A little bit of housekeeping
        
        for layer in self.temp_layers:
            self.__class__(layer).Clean()  #Clean layer if it was temporary
            #utils.shell_command(["container","clean",layer])

    def command_Chroot(self):
        
        stopped=False
        if "Stopped" in self.Status():
            stopped=True
             
        if stopped:
            self.Start()
            while not os.listdir("merged"): #Wait until merged directory has files before you attempt to chroot
                pass
                
            while not self.setup:
                try:
                    self._load()
                except ValueError:
                    pass
                    
        command=self.shell #By default, run the shell
        if "run" in self.flags:
            command=self.flags["run"] 
        utils.shell_command(chroot_command(self,command),stdout=None)
        
        if stopped: #Return to previous state
            self.Stop()
        
        if "and-stop" in self.flags:
            return self.Stop() 
    
    def command_Prune(self):
        containers=get_all_items(self.directory)
        layers={'container':[],"folder":[]}
        
        for _ in containers:
            try:
                with open(os.path.join(self.directory,_,"docker.json")) as f:
                    layers['container'].extend(json.load(f)["layers"])
            except FileNotFoundError: #If docker.json doesn't exist for any container, root is not a Docker registry, and can be safely ignored
                return
                
        for _ in os.listdir(self.directory):
            if os.listdir(os.path.join(self.directory,_))==['diff']: #All non-container layers
                layers['folder'].append(os.path.join(self.name,utils.filename_to_name(_)))
        
        if layers['folder']==[]: #If there are no layers, then root is not a registry any can be safely ignored
            return
            
        difference=[_ for _ in layers['folder'] if _ not in layers['container']] #Get unused layers
        print("Layers to be deleted:",difference)
        
        for _ in difference:
            self.__class__(_).Delete()

    def command_Init(self):
        if "pull" in self.flags:
            Import(self.original_name,self.ROOT)
            
            if "dockerfile" in self.flags:
                Convert(self.flags["dockerfile"],self.directory)
                
        os.makedirs(self.directory,exist_ok=True)
        os.chdir(self.directory)
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
            
        if utils.check_if_any_element_is_in_list(['only-chroot','and-chroot'],self.flags):
            return [self.Start(),self.Delete() if 'temp' in self.flags else None]

    def command_Edit(self):
        if 'build' in self.flags:
            utils.shell_command([os.getenv("EDITOR","vi"),os.path.join(self.directory,"Containerfile.py")],stdout=None)
        else:
            utils.shell_command([os.getenv("EDITOR","vi"),os.path.join(self.directory,"container-compose.py")],stdout=None)
    
    def command_Clean(self):
        self.Stop()
        os.system(f"sudo rm -rf diff{os.sep}*")
    
    def command_Delete(self): #Probably move into the parent implementation
        self.Stop()
        utils.shell_command(["sudo","rm","-rf",self.directory])
        
        parent_dir=os.path.dirname(self.directory)
        while parent_dir != self.ROOT:
            if os.path.exists(parent_dir) and len(os.listdir(parent_dir))==0:
                os.rmdir(parent_dir)
            else:
                break
            parent_dir=os.path.dirname(parent_dir)
        
        
        
        if 'auto-pune-experimental' in self.flags:
            if 'no-prune' not in self.flags:
                os.chdir(self.ROOT) #Since the current directory doesn't exist anymore, which messes up utils.set_directory
                self.__class__(self.name.split('/')[0]).Prune()
                

def main():
    utils.parse_and_call_and_return(Container)
        

    
