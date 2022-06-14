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

# < include utils.py >

import utils
CLASS_NAME="Container"

utils.ROOT=ROOT=utils.get_root_directory(CLASS_NAME)
utils.TEMPDIR=TEMPDIR=utils.get_tempdir()

NAMES,FLAGS,FUNCTION=utils.extract_arguments()

utils.NAMES=NAMES
utils.ROOT=ROOT
utils.GLOBALS=globals()

SHELL_CWD=os.environ.get("PWD")
PATH="PATH=/bin:/usr/sbin:/sbin:/usr/bin"

#Helper functions
def list_containers(*args, **kwargs):
    return utils.list_items_in_root(*args, FLAGS,CLASS_NAME,**kwargs)    

def flatten(*args, **kwargs):
    return utils.flatten_list(*args, **kwargs)

def print_result(*args, **kwargs):
    return utils.print_list(*args, **kwargs)

def convert_colon_string_to_directory(string):
    string=utils.split_string_by_char(string,char=":")
    if string[0]=="root":
        string=string[1] #The directory is just the absolute path in the host
    elif len(string)==1:
        string=string[0] # No container was specified, so assume "root"
    else:
        string=f"{ROOT}/{string[0]}/diff{string[1]}" # Container was specified, so use it
    string=os.path.expanduser(string)
    return string
    
def load_dependencies(layer):
    with open(f"{ROOT}/{layer}/container-compose.py") as fh:        
       root = ast.parse(fh.read())
       for node in ast.iter_child_nodes(root):
           if isinstance(node, ast.Expr) and isinstance(node.value,ast.Call):
               function=node.value.func.id
               if function in ["Layer","Base","Env"]:
                   arguments=[eval(ast.unparse(val)) for val in node.value.args]
                   globals()[function](*arguments)

def remove_empty_folders_in_diff():
    walk = list(os.walk("diff"))
    for path, _, _ in walk[::-1]:
        if not path.startswith("diff/.unionfs"):
            if len(os.listdir(path)) == 0:
                os.rmdir(path)
                
ContainerDoesNotExist=utils.DoesNotExist
class Container:
    def __init__(self,_name,_flags=None,_unionopts=None,_workdir='/',_env=None,_function=None,_uid=None,_gid=None,_shell=None):
        self.Class = utils.Class(self,CLASS_NAME.lower())
        self.Class.class_init(_name,_flags,_function,_workdir)
        
        self.base="void"
        
        self.unionopts=utils.get_value(_unionopts,"diff=RW")
        
        self.env=utils.get_value(_env,f"export {PATH}")
        
        #Whether we mounted dev, proc, etc.
        self.mounted_special=False
            
        self.workdir=_workdir
        
        self.uid=utils.get_value(_uid,os.getuid())
        self.gid=utils.get_value(_gid,os.getgid())
        
        self.shell=utils.get_value(_shell,"/bin/bash")
        
        self.temp_layers=[]
        
        self.hardlinks=[]
    
    #Functions
    def Run(self,command="",pipe=False):
        if self.function=="build":
            if command.strip()!="":
                print(f"Command: {command}")
        self.Base(self.base)
        #Only mount if this is the first Run called, where the base hasn't been added to unionopts
        if not self.unionopts.endswith(f":{ROOT}/{self.base}/diff=RO"):
            self.unionopts+=f":{ROOT}/{self.base}/diff=RO"
            
            #Prevent merged from being mounted multiple times
            if not os.path.ismount("merged"):
                utils.shell_command(["unionfs","-o","allow_other,cow,hide_meta_files",f"{self.unionopts}","merged"])
               
        #Mount dev,proc, etc. over the unionfs to deal with mmap bugs (fuse may be patched to deal with this natively so I can just mount on the diff directory, but for now, this is what is needed)
        if not self.mounted_special:
            for dir in ["dev","proc","sys","run"]:
                if not os.path.ismount(f"merged/{dir}"):
                    #Use bind mounts for special mounts, as bindfs has too many quirks (and I'm using sudo regardless)
                    if sys.platform=="darwin":
                        #MacOS doesn't have bind-mounts, and direct_io doesn't affect them
                        utils.shell_command(["sudo", "bindfs", "-o", "direct_io,allow_other,dev", f"/{dir}", f"merged/{dir}"])
                    elif sys.platform=="cygwin":
                        #Cygwin doesn't have rbind
                        utils.shell_command(["sudo","mount","-o","bind",f"/{dir}",f"merged/{dir}"])
                    else:
                        utils.shell_command(["sudo","mount","--rbind",f"/{dir}",f"merged/{dir}"])
                   
            self.mounted_special=True
            
        with open(f"{TEMPDIR}/container_{self.name}.log","a+") as log_file:
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
            return utils.shell_command(["sudo","nohup","chroot",f"--userspec={self.uid}:{self.gid}", "merged",f"{self.shell}","-c",f"{self.env}; cd {self.workdir}; {command}"],stdout=stdout,stderr=stderr)
            
    
    def Ps(self,process=None):
        if process=="main" or ("--main" in self.flags):
            return self.Class.get_main_process()
        elif process=="auxiliary" or ("--auxiliary" in self.flags):
            if not os.path.isdir("merged"):
                return []
            processes=[_[1:] for _ in utils.shell_command(["lsof","-Fp","-w","--","merged"]).splitlines()]
            return list(map(int,processes))
    
    def Mount(self,IN,OUT):
        try:
            os.makedirs(f"diff{OUT}",exist_ok=True)
        except FileExistsError:
            os.remove(f"diff{OUT}")
            os.makedirs(f"diff{OUT}",exist_ok=True)
        if not os.path.ismount(f"diff{OUT}"):
            IN=convert_colon_string_to_directory(IN)
            utils.shell_command(["bindfs",IN,f"diff{OUT}"])
    
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
        
        #print(f"cp -a {src} {dest}")                   
        cp_error=utils.shell_command(["cp","-a",f"{src}",f"{dest}"])
        if "cp: cannot create" in cp_error:
            #dest does not exist, so create its parent's folder
            os.makedirs(os.path.dirname(dest),exist_ok=True)
            utils.shell_command(["cp","-a",f"{src}",f"{dest}"])

    def Loop(self,*args, **kwargs):
        self.Class.loop(*args, **kwargs)
        #Run(f'(while true; do "{command}"; sleep {delay}; done)')
        
    def Base(self,base):
        
        #Effectively make subsequent Bases a no-op
        if not self.unionopts.endswith(f":{ROOT}/{self.base}/diff=RO"):
            self.base=base
    
    def Wait(self,*args, **kwargs):
        utils.wait(*args, **kwargs)

    def Layer(self,layer,mode="RO"):
        if self.function=="build":
            if len(os.listdir(f"{ROOT}/{layer}/diff"))<2:
                utils.shell_command(["container","build",layer]) #Build layer if it doesn't exist
                self.temp_layers.append(layer) #Layer wasn't needed before so we can delete it after
        load_dependencies(layer)
        self.unionopts+=f":{ROOT}/{layer}/diff={mode}"
    
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
        volume_path=f"{ROOT}/{name[0]}/Volumes/{name[1]}"
        
        #If a directory, just mount it directly. If file, hardlink it
        if os.path.isdir(volume_path):
            self.Mount(volume_path,path)
        else:
            try:
                os.link(volume_path,f"diff/{path}")
            except FileExistsError:
                os.remove(f"diff/{path}")
                os.link(volume_path,f"diff/{path}")
            self.hardlinks.append(f"diff/{path}")
        
    def Update(self,keys):
        if self.function=="build":
            return #No lock file when building --- no need for it
        if isinstance(keys,str):
            keys=[keys]
        
        with open(f"{TEMPDIR}/container_{self.name}.lock","r") as f:
            data=json.load(f)
            
        for key in keys:
            data[key]=getattr(self,key)
        
        with open(f"{TEMPDIR}/container_{self.name}.lock","w+") as f:
            json.dump(data,f)  
        
    #Commands      
    def Start(self):
        if "Started" in self.Status():
            return f"Container {self.name} is already started"
        
        #Fork process, so it can run in the background
        pid=os.fork()
        
        #If child, run code, then exit 
        if pid==0:
            self.Base(self.base)
            with open(f"{TEMPDIR}/container_{self.name}.log","a+") as f:
                pass
            #Open a lock file so I can find it with lsof later
            self.lock=open(f"{TEMPDIR}/container_{self.name}.lock","w+")
            
            with open(f"{TEMPDIR}/container_{self.name}.lock","w+") as f:
                json.dump({},f)
            
            self.Update(["env","workdir", "uid","gid","shell"])
            
            #Run *service.py
            with open(f"{ROOT}/{self.name}/container-compose.py") as f:
                code=f.read()
            exec(code,globals(),locals())
            
            #Don't have to put Run() in container-compose.py just to start it
            self.Run()
            self.Wait()
            exit()
        
    def Build(self):
        self.Stop()
        with open("Containerfile.py") as f:
         code = compile(f.read(), 'Containerfile.py', 'exec')
         exec(code,globals(),locals())
        self.Stop()
        remove_empty_folders_in_diff()
        for layer in self.temp_layers:
            utils.shell_command(["container","clean",layer]) #Clean layer if it was temporary
        
       
    def Stop(self):
        output=[self.Class.stop()]
        #Unmount dev,proc, etc. if directory exists
        if os.path.isdir("merged"):
            for dir in os.listdir("merged"):
                if os.path.ismount(f"merged/{dir}"):
                
                    utils.shell_command(["sudo","mount","--make-rslave",f"merged/{dir}"])
                    utils.shell_command(["sudo","umount","-R","-l",f"merged/{dir}"])
                    
        
        diff_directories=[utils.split_string_by_char(_," ")[2] for _ in utils.shell_command(["mount"]).splitlines() if f"{ROOT}/{self.name}/diff" in _]
        for dir in diff_directories:
             utils.shell_command(["umount","-l",dir])
             utils.shell_command(["rm","-rf",dir])
        utils.shell_command(["umount","-l","merged"])
    
        
        for hardlink in self.hardlinks:
            os.remove(hardlink) #Remove volume hardlinks when done
        self.Class.cleanup_after_stop()
        return output

    def Restart(self):
        return self.Class.restart()
    
    def Chroot(self):
        if "Stopped" in self.Status():
            return ["Can't chroot into stopped container!"]
        
        with open(f"{TEMPDIR}/container_{self.name}.lock","r") as f:
            data=json.load(f)
        
        command=None
        for flag in self.flags:
            if flag.startswith("--run="):
                command=flag.split("=",1)[1]
        
        if not command:
            command=""
        else:
            command=f"-c '{command}'"
        for key in data:
            setattr(self,key,data[key])
        utils.shell_command(["sudo","chroot",f"--userspec={self.uid}:{self.gid}",f"{ROOT}/{self.name}/merged","/bin/sh","-c",f"""{self.env}; cd {self.workdir}; {self.shell} {command}"""],stdout=None)
        
        #For some reason, only os.system doesn't use the PS1
        #os.system(f"sudo chroot --userspec={self.uid}:{self.gid} {ROOT}/{self.name}/merged /bin/sh -c '{self.env}; cd {self.workdir}; {self.shell}'")
        
        if "--and-stop" in self.flags:
            return [self.Stop()]
    
    
    def List(self):
        return self.Class.list()

    def Init(self):
       
        os.makedirs(f"{ROOT}/{self.name}",exist_ok=True)
        os.chdir(f"{ROOT}/{self.name}")
        os.makedirs("diff",exist_ok=True)
        os.makedirs("merged",exist_ok=True)
        
        if '--temp' in self.flags:
            self.flags.append('--no-edit')
            self.flags.append('--only-chroot')
        with open(f"container-compose.py",'a'):
            pass
        
        if '--build' in self.flags:
            with open(f"Containerfile",'a'):
                pass
        
        if '--no-edit' not in self.flags:
            self.Edit()
        
        if utils.check_if_element_any_is_in_list(['--only-chroot','--and-chroot'],self.flags):
            return [self.Start(),self.Delete() if '--temp' in self.flags else None]

    def Edit(self):
        if '--build' in self.flags:
            utils.shell_command([os.getenv("EDITOR","vi"),f"{ROOT}/{self.name}/Containerfile.py"],stdout=None)
        else:
            utils.shell_command([os.getenv("EDITOR","vi"),f"{ROOT}/{self.name}/container-compose.py"],stdout=None)

    def Status(self):
        return self.Class.status()

    def Log(self):
        self.Class.log()
    
    def Clean(self):
        self.Stop()
        os.system(f"sudo rm -rf diff/*")
    
    def Delete(self):
        self.Stop()
        utils.shell_command(["sudo","rm","-rf",f"{ROOT}/{self.name}"])
    
    def Watch(self):
        self.Class.watch()

NAMES=list_containers(utils.NAMES)
for name in NAMES:
    
    BASE="void"
    UNIONOPTS="diff=RW"
    
    try:
        container=Container(name,_flags=FLAGS,_unionopts=UNIONOPTS,_function=FUNCTION)
    except ContainerDoesNotExist:
        print(f"Container {name} does not exist")
        continue
        
    utils.export_methods_globally(CLASS_NAME)
    result=utils.execute_class_method(eval(f"{CLASS_NAME.lower()}"),FUNCTION)
    
    print_result(result)
        

    
