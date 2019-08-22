function detectCapsLock(event)
{
    var e = event||window.event;
    var keyCode = e.keyCode||e.which; // 按键的keyCode 
    var isShift = e.shiftKey ||(keyCode ==   16 ) || false ; // shift键是否按住
	// Caps Lock 打开，且没有按住shift键 
	// Caps Lock 打开，且按住shift键
	if(((keyCode >= 65 && keyCode <= 90 ) && !isShift) || ((keyCode >= 97 && keyCode <= 122 ) && isShift))
	{
		return true;
	}
	else
	{
		return false;
	} 
}

<!--//cc找的等待窗口js-->
//args 为传递的参数 格式为{arg1:"",arg2:""} w为窗口的宽 h为窗口的高
function getValueWaitWin(obj)
{
	var st=document.documentElement.scrollTop;//滚动条距顶部的距离
	var sl=document.documentElement.scrollLeft;//滚动条距左边的距离
	var ch=document.documentElement.clientHeight;//屏幕的高度
	var cw=document.documentElement.clientWidth;//屏幕的宽度
	var objH=$("#"+obj).height();//浮动对象的高度
	var objW=$("#"+obj).width();//浮动对象的宽度
	var objT=Number(st)+(Number(ch)-Number(objH))/2;
	var objL=Number(sl)+(Number(cw)-Number(objW))/2;
	return objT+"|"+objL;
}
function showWaitWin(w,h,args)
{
	if(w="" || typeof(w)=="undefined"){
		var w=600;
	}
	if(h="" || typeof(h)=="undefined"){
		var h=250;
	}
	var bH=$("body").height();
	var bW=$("body").width();
	$("#msgWaitWin").height(h);
	$("#msgWaitWin").width(w);
	var objWH=getValueWaitWin("msgWaitWin");
	$("#fullBgWaitWin").css({width:bW,height:bH,display:"block"});
	var tbT=objWH.split("|")[0]+"px";
	var tbL=objWH.split("|")[1]+"px";
	$("#msgWaitWin").css({top:tbT,left:tbL,display:"block"});
}

//关闭灰色背景和操作窗口
function closeWaitWin()
{
	$("#fullBgWaitWin").css("display","none");
	$("#msgWaitWin").css("display","none");
}
<!--cc找的等待窗口结束-->


