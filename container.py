#!/usr/bin/env python
import subprocess
import re
import sys
import os
import threading
import time
import ast
import pwd, grp

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

def split_by_char(*args, **kwargs):
    return utils.split_string_by_char(*args, **kwargs)
    
def shell_command(*args, **kwargs):
    return utils.shell_command(*args, **kwargs)

def convert_colon_string_to_directory(string):
    string=split_by_char(string)
    if string[0]=="root":
        string=string[1]
    elif len(string)==1:
        string=string[0]
    else:
        string=f"{ROOT}/{string[0]}/diff{string[1]}"
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
    def __init__(self,_name,_flags=None,_unionopts=None,_workdir='/',_env=None,_function=None,_uid=None,_gid=None,shell=None):
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
        
        self._shell=utils.get_value(_shell,"/bin/bash")
    
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
                shell_command(["unionfs","-o","allow_other,cow,hide_meta_files",f"{self.unionopts}","merged"])
               
        #Mount dev,proc, etc. over the unionfs to deal with mmap bugs (fuse may be patched to deal with this natively, but for now, this is what is needed)
        if not self.mounted_special:
            for dir in ["dev","proc","sys","run"]:
                if not os.path.ismount(f"merged/{dir}"):
                    #Use bind mounts for special mounts, as bindfs has too many quirks (and I'm using sudo regardless)
                    #shell_command(f"sudo bindfs -o direct_io,allow_other,dev /{dir} merged/{dir}")
                    shell_command(["sudo","mount","--rbind",f"/{dir}",f"merged/{dir}"])
                   
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
            return shell_command(["sudo","nohup","chroot",f"--userspec={self.uid}:{self.gid}", "merged",f"{self.shell}","-c",f"{self.env}; cd {self.workdir}; {command}"],stdout=stdout,stderr=stderr)
            
    
    def Ps(self,process="auxiliary"):
        if process=="main":
            return self.Class.get_main_process()
        elif process=="auxiliary":
            if not os.path.isdir("merged"):
                return []
            processes=[_[1:] for _ in shell_command(["lsof","-Fp","-w","--","merged"]).splitlines()]
            return list(map(int,processes))
    
    def Mount(self,IN,OUT):
        os.makedirs(f"diff{OUT}",exist_ok=True)
        if not os.path.ismount(f"diff{OUT}"):
            IN=convert_colon_string_to_directory(IN)
            shell_command(["bindfs",IN,f"diff{OUT}"])
    
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
        cp_error=shell_command(["cp","-a",f"{src}",f"{dest}"])
        if "cp: cannot create" in cp_error:
            #dest does not exist, so create its parent's folder
            os.makedirs(os.path.dirname(dest),exist_ok=True)
            shell_command(["cp","-a",f"{src}",f"{dest}"])

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
        load_dependencies(layer)
        self.unionopts+=f":{ROOT}/{layer}/diff={mode}"
    
    def Workdir(self,*args, **kwargs):
        self.Class.workdir(*args, **kwargs)
        os.makedirs(f"diff{self.workdir}",exist_ok=True)
    
    def Env(self,*args, **kwargs):
        self.env=utils.add_environment_variable_to_string(self.env,*args, **kwargs)
    
    def User(self,user=""):
        if user=="":
            self.uid=os.getuid()
            self.gid=os.getgid()
        else:
            user=split_string_by_char(user,char=":")
            if len(user)==1:
                user.append(user[0])
            if user[0].isnumeric():
                self.uid=user[0]
            else:
                self.uid=pwd.getpwnam(user[0])[2]
            
            if user[1].isnumeric():
                self.gid=user[1]
            else:
                self.gid=pwd.getpwnam(user[1])[2]
    
    def Shell(self,shell):
        self.shell=shell        
        
    #Commands      
    def Start(self):
        
        self.Base(self.base)
        if "Started" in self.Status():
            return f"Container {self.name} is already started"
        
        #Fork process, so it can run in the background
        pid=os.fork()
        
        #If child, run code, then exit 
        if pid==0:
            #Open a lock file so I can find it with lsof later
            lock_file=open(f"{TEMPDIR}/container_{self.name}.lock","w+")
            #Run *service.py
            with open(f"{ROOT}/{self.name}/container-compose.py") as f:
                code=f.read()
            exec(code,globals(),locals())
            
            #Don't have to put Run() in container-compose.py just to start it
            self.Run()
            self.Wait()
            exit()
        if "--only-chroot" in self.flags:
            return [self.Chroot(), self.Stop()]
        elif "--and-chroot" in self.flags:
            return [self.Chroot()]
        
    def Build(self):
        self.Stop()
        with open("Containerfile") as f:
         code = compile(f.read(), 'Containerfile', 'exec')
         exec(code,globals(),locals())
        self.Stop()
        remove_empty_folders_in_diff()
        
       
    def Stop(self):
        output=[self.Class.stop()]
        #Unmount dev,proc, etc. if directory exists
        if os.path.isdir("merged"):
            for dir in os.listdir("merged"):
                if os.path.ismount(f"merged/{dir}"):
                
                    shell_command(["sudo","mount","--make-rslave",f"merged/{dir}"])
                    shell_command(["sudo","umount","-R","-l",f"merged/{dir}"])
                    
        
        diff_directories=[split_by_char(_," ")[2] for _ in shell_command(["mount"]).splitlines() if f"{ROOT}/{self.name}/diff" in _]
        for dir in diff_directories:
             shell_command(["umount","-l",f"{dir}"])
        shell_command(["umount","-l","merged"])
    
    
        self.Class.cleanup_after_stop()
        return output

    def Restart(self):
        return self.Class.restart()
    
    def Chroot(self):
        if self.flags==[] and ("Stopped" in self.Status()):
            self.flags+=['--only-chroot']
            #Prevent zombie processes, as parent process is still up when forked process ends. So, just ignore it.
            import signal
            signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            return [self.Start()]
        os.system(f" {self.env}; sudo chroot --userspec=$(id -u):$(id -g) {ROOT}/{self.name}/merged {SHELL}")
    
    
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
            shell_command([os.getenv("EDITOR","vi"),f"{ROOT}/{self.name}/Containerfile"],stdout=None)
        else:
            shell_command([os.getenv("EDITOR","vi"),f"{ROOT}/{self.name}/container-compose.py"],stdout=None)

    def Status(self):
        return self.Class.status()

    def Log(self):
        self.Class.log()
    
    def Clean(self):
        self.Stop()
        os.system(f"sudo rm -rf diff/*")
    
    def Delete(self):
        self.Stop()
        shell_command(["sudo","rm","-rf",f"{ROOT}/{self.name}"])
    
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
        

    
