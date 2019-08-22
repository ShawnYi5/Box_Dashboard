// JavaScript Document
//中文
if(typeof (String.prototype.trim) != "function" ) {
	String.prototype.trim = function () {
		return this.replace(/(^\s*)|(\s*$)/g, "");
	}
}
function removespace(obj) {
	arry = ['test_timeinterval','test_frequency']
	if($.inArray(obj.id,arry) >= 0){
		if(parseInt(obj.value) < 1){
			obj.value = 10
		}
	}
	if(obj.type =="number"){
		obj.value = parseInt(obj.value);
	}
	check_ip_format_from_input(obj);
    obj.value = obj.value.trim();
}

function fixedInt(obj, init, allow) {
	var cur_num = obj.value;
	if(cur_num === ''){
		return obj.value = init;
	}
	cur_num = parseInt(cur_num);

	if(cur_num === allow){
		return obj.value = cur_num;
	}
	if(cur_num > parseInt(obj.max)){
		return obj.value = parseInt(obj.max);
	}
	if(cur_num < parseInt(obj.min)){
		return obj.value = parseInt(obj.min);
	}
	return obj.value = cur_num;
}

function fixedIntBy8(obj, init) {
	fixedInt(obj, init);
	return obj.value = parseInt(obj.value /8 ) * 8;
}

function check_ip_format_from_input(obj){
	var check_value = [];
	var check_type = ['ip','mask','gateway','dns','route_ip','route_mask','route_gateway','service_ip','control_ip','route_gate'];
	if ($('input[name=switch_change_master_ip]').is(':checked'))
	{
		check_value = ['ip','子网掩码','默认网关','DNS','目标网络','网络(子网)掩码','网关','飘移IP','固有IP','网关'];
	}
	else
	{
		check_value = ['ip','子网掩码','默认网关','DNS','目标网络','网络(子网)掩码','网关','IP','IP','网关'];
	}
	var is_ckeck = -10;
	if(obj.tagName == 'INPUT'){
		if(check_type.indexOf(obj.className)>=0){
			is_ckeck = check_type.indexOf(obj.className);
		}else if(check_type.indexOf(obj.name)>=0){
			is_ckeck = check_type.indexOf(obj.name);
		}
	}
	if(is_ckeck >= 0){
		// 空值不验证，影响体验
		if(obj.value && !isIPV4(obj.value)){
			$(obj).addClass('input-is-invalid');
			// openWarningDialog({title:'网络配置无效',html:'请输入有效的“'+ check_value[is_ckeck] +'”地址'});
		}else{
			$(obj).removeClass('input-is-invalid');
		}
	}
}
function Num64(a)
{
	this.num=[];
	for(var i=0;i<a.length;i++){
		this.num.push(a[i]);
	}
}
Num64.prototype.compare=function(x){
	var a=this.num.length;
	var b=x.num.length;
	if(a>b)
	{
		return 1;
	}
	else if(a<b)
	{
		return -1;
	}
	else
	{
		for(var j=0;j<a;j++)
		{
			if(this.num[j]>x.num[j])
			{
				return 1;
			}
			else if(this.num[j]<x.num[j])
			{
				return -1;
			}
		}
	}
	return 0;
}

function isEmail(input){
	var reg = /^\w+((-\w+)|(\.\w+))*\@[A-Za-z0-9]+((\.|-)[A-Za-z0-9]+)*\.[A-Za-z0-9]+$/;;
	if( reg.test(input) )
	{
		return true;
	}
	else
	{
		return false;
	}
}

function isIPv6(str)//IPV6地址判断 
{ 
	 return /:/.test(str) 
		 &&str.match(/:/g).length<8
		 &&/::/.test(str)
		 ?(str.match(/::/g).length==1
		 &&/^::$|^(::)?([\da-f]{1,4}(:|::))*[\da-f]{1,4}(:|::)?$/i.test(str))
		 :/^([\da-f]{1,4}:){7}[\da-f]{1,4}$/i.test(str);
}

function isIPV4(ip)
{
	var re=/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/;//正则表达式
   if(re.test(ip))
   {
       if( RegExp.$1<256 && RegExp.$2<256 && RegExp.$3<256 && RegExp.$4<256)
       return true;
   }
   return isIPv6(ip);
}

function isNFSpath(ip)
{
   var re=/^\w*\W*.*:\/\w*\W*/;
   if(re.test(ip))
   {
       return true;
   }
   return false;
}

function isCIFSpath(ip)
{
   var re=/^\\\\\w*\W*.*\\\w*\W*/;
   if(re.test(ip))
   {
       return true;
   }
   return false;
}

function isMask(mask) {

	var re=/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/;//正则表达式
	var mask_num = ['0', '128', '192', '224', '240', '248', '252', '254', '255'];
	var c = function (v) {
		return mask_num.indexOf(v) != -1;
    }
	if(re.test(mask))
	{
	   if( c(RegExp.$1) && c(RegExp.$2) && c(RegExp.$3) && c(RegExp.$4))
	   return true;
	}
	return false;
}

function isNum(input)
{
	var patrn=/^\d+$/;
	if( patrn.exec(input) )
	{
		return true;
	}
	else
	{
		return false;
	}
}

function isTime(input)
{
	//判断是不是日期和时间的：yyyy-MM-dd HH:CC:SS.000000
	var patrn= /^([1][7-9][0-9][0-9]|[2][0][0-9][0-9])(\-)([0][1-9]|[1][0-2])(\-)([0][1-9]|[1-2][0-9]|[3][0-1])(.)([0-1][0-9]|[2][0-3])(:)([0-5][0-9])(:)([0-5][0-9])(\.)(\d{6})$/g;
	if( patrn.exec(input) )
	{
		return true;
	}
	else
	{
		return false;
	}
}

function isFloat(input)
{
	var re = /^[0-9,-]+.?[0-9]*$/;
	if (!re.test(input))
	{
		return false;
	}
	return true;
}

function isEmpty(input)
{
	if( input.trim() == '' )
	{
		return true;
	}
	return false;
}

function showHide(szName,intblock)
{
	var obj = document.getElementById(szName);
	if( 0 == intblock )
	{
		if( obj.style.display == 'none' || obj.style.display == '')
		{
			obj.style.display = 'block';
		}
		else
		{
			obj.style.display = 'none';
		}
	}
	else
	{
		if( obj.style.display == 'block' || obj.style.display == '')
		{
			obj.style.display = 'none';
		}
		else
		{
			obj.style.display = 'block';
		}
	}
}

function myArray()
{
	this.length=arguments.length;
	for(var i=0;i<this.length;i++)
	{
		this[i]=arguments[i];
	}
}

function onDisableEnable(szArray)
{
	for( i =0;i< szArray.length;i++)
	{
		var elem = document.getElementById(szArray[i])
		elem.disabled = !elem.disabled;
	}
}




function move(szNameSrc,szNameDes)
{
	var src = document.getElementById(szNameSrc);
	var des = document.getElementById(szNameDes);

	if (src.selectedIndex == -1)
		return false;

	for(i = 0;i< src.options.length;i++)
	{
		var index = src.selectedIndex
		if( src.options[i].selected == true )
		{
			var text = src.options[index].text;
			var value = src.options[index].value;
			des.options.add(new Option(text,value,false,true));
			src.removeChild(src.options[index]);
		}
		//move(szNameSrc,szNameDes);
	}
}

function moveAll(szNameSrc,szNameDes)
{
	var src = document.getElementById (szNameSrc);
	var des = document.getElementById (szNameDes);

	if(0 > src.options.length)
		return;

	var index = src.options.length;

	for (j = 0;j < index; j++)
	{
		var text = src.options[0].text;
		var value = src.options[0].value;
		des.options.add(new Option(text,value,false,true)) ;
		src.removeChild(src.options[0]);
	}
}

function onDEnable(obj,szArrayEnable,szArrayDisable)
{

	var objFind;
	var bDEnable = true;

	if( obj.checked || obj.selected )
	{
		bDEnable = false;
	}

	for( i =0;i< szArrayEnable.length;i++)
	{
		var elem = document.getElementById(szArrayEnable[i])
		elem.disabled = bDEnable;
	}
	if(typeof(szArrayDisable) !='undefined')
	{
		for( i =0;i< szArrayDisable.length;i++)
		{
			elem = document.getElementById(szArrayDisable[i])
			elem.disabled = !bDEnable;
		}
	}
}


function removePrinterList()
{
	var obj = document.getElementById('srcList');
	var index = obj.options.length;

	for (j = 0;j < index; j++)
	{
		obj.removeChild(obj.options[0]);
	}
}

function removeById(id)
{
	var obj = document.getElementById(id);
	var index = obj.options.length;

	for (j = 0;j < index; j++)
	{
		obj.removeChild(obj.options[0]);
	}
}

function getCheckedValue(name)
{
	if( $("#" + name).attr("checked") )
	{
		return $("#" + name).attr("value");
	}
	else
	{
		return 0;
	}
}

//弹出一个确认对话框
function openConfirmDialog(option){
	var o = $.extend({
		title: '操作确认',
		html: '操作确认',
		width: 380,
		height: 200,
		confirm_text: '确认',
		cancel_text: '取消',
		onBeforeOK: function(){},
		onCancel: function(){}
    }, option || {});

	checkDialogForm();

	$('#dialog-confirm .dialog-icon').html('<img src="/static/images/tip_icon_ask.png" width="45"/>');
	$('#dialog-confirm .dialog-msg').html(o.html);
	setTimeout(function(){
		$("#dialog-confirm .dialog-icon").pngFix();
	},13);

	//$('#dialog-confirm').dialog("destroy");
	$('#dialog-confirm').dialog({
		modal: true,
		title: o.title,
		width: o.width,
		height: o.height,
		buttons:[{
			text:o.confirm_text,
			click: o.onBeforeOK
			},
			{
			text:o.cancel_text,
			click: function(){
				o.onCancel();
				$(this).dialog('close');
			}
		}],
		close: function(){},
		open: function() {
    		$(this).closest( ".ui-dialog" ).find(":button").blur();
		}
	});
}

//弹出一个警告对话框
function openWarningDialog(option,html){
	var o = $.extend({
		title: '警告信息',
		html: '警告信息',
		width: 380,
		height: 200
	});

	if (typeof(option) == "object") {
		o = $.extend(o, option);
	}else{
		if(option) o.title = option;
		if(html) o.html = html;
	}

	// 只markdown 非 html字符串
	if (!is_html_str(o.html)){
		o.html = markdown.toHTML(o.html);
	}

	checkDialogForm();

	//$('#dialog-confirm').dialog("destroy");
	$('#dialog-confirm .dialog-icon').html('<img src="/static/images/tip_icon_warning.png" width="45"/>');
	$('#dialog-confirm .dialog-msg').html(o.html);
	setTimeout(function(){
		$("#dialog-confirm .dialog-icon").pngFix();
	},13);

	$('#dialog-confirm').dialog({
		modal: true,
		title: o.title,
		width: o.width,
		height: o.height,
		buttons: {
			'确定': function(){
				$(this).dialog('close');
			}
		},
		close: function(){}
	});
}

//弹出一个等待对话框
function openWaitingDialog(option,html){
	var o = $.extend({
		title: '提示信息',
		html: '提示信息',
		width: 380,
		height: 200
	});

	if (typeof(option) == "object") {
		o = $.extend(o, option);
	}else{
		//if(option) o.title = option;
		if(html) o.html = html;
	}
	// 只markdown 非 html字符串
	if (!is_html_str(o.html)){
		o.html = markdown.toHTML(o.html);
	}

	checkDialogForm();

	$('#dialog-confirm').dialog("destroy");
	$('#dialog-confirm .dialog-icon').html('<img src="./images/tip_icon_loading.gif" width="16"/>');
	$('#dialog-confirm .dialog-msg').html(o.html);
	setTimeout(function(){
		$("#dialog-confirm .dialog-icon").pngFix();
	},13);

	$('#dialog-confirm').dialog({
		modal: true,
		width: o.width,
		height: o.height,
		dialogClass: "my-dialog"
	});
}

function is_html_str(str) {
	return $('<div></div>').html(str).children().length != 0;
}

//弹出一个错误对话框
function openErrorDialog(option,html){
	var o = $.extend({
		title: '错误信息',
		html: '错误信息',
		onBeforeOK: function(){},
		width: 380,
		height: 250
	});

	if (typeof(option) == "object") {
		o = $.extend(o, option);
	}else{
		if(option) o.title = option;
		if(html) o.html = html;
	}
	// 只markdown 非 html字符串
	if (!is_html_str(o.html)){
		o.html = markdown.toHTML(o.html);
	}

	checkDialogForm();

	$('#dialog-confirm .dialog-icon').html('<img src="/static/images/tip_icon_error.png" width="45"/>');
	$('#dialog-confirm .dialog-msg').html(o.html);
	setTimeout(function(){
		$("#dialog-confirm .dialog-icon").pngFix();
		//$('#dialog-confirm').dialog("destroy");
		$('#dialog-confirm').dialog({
			modal: true,
			title: o.title,
			width: o.width,
			height: o.height,
			buttons: {
				'确定': function(){
					o.onBeforeOK();
					$(this).dialog('close');
				}
			},
			close: function(){
				o.onBeforeOK();
				$('#dialog-confirm .dialog-icon').html('');
				$('#dialog-confirm .dialog-msg').html('');
			}
		});

	},13);
}

//弹出一个成功对话框
function openSuccessDialog(option,html){
	var o = $.extend({
		title: '成功信息',
		html: '成功信息',
		onBeforeOK: function(){},
		width: 380,
		height: 200
	});

	if (typeof(option) == "object") {
		o = $.extend(o, option);
	}else{
		if(option) o.title = option;
		if(html) o.html = html;
	}
	// 只markdown 非 html字符串
	if (!is_html_str(o.html)){
		o.html = markdown.toHTML(o.html);
	}

	checkDialogForm();

	$('#dialog-confirm .dialog-icon').html('<img src="/static/images/tip_icon_success.png" width="45"/>');
	$('#dialog-confirm .dialog-msg').html(o.html);
	setTimeout(function(){
		$("#dialog-confirm .dialog-icon").pngFix();

		$('#dialog-confirm').dialog({
			modal: true,
			title: o.title,
			width: o.width,
			height: o.height,
			buttons: {
				'确定': function(){
					o.onBeforeOK();
					$(this).dialog('close');
				}
			},
			close: function(){}
		});
	},13);

	
}

//弹出一个成功对话框
function openCommonDialog(option,html){
	var o = $.extend({
		title: '提示信息',
		html: '提示信息',
		width: 380,
		height: 200,
		button_name:'确定'
	});

	if (typeof(option) == "object") {
		o = $.extend(o, option);
	}else{
		if(option) o.title = option;
		if(html) o.html = html;
	}
	// 只markdown 非 html字符串
	if (!is_html_str(o.html)){
		o.html = markdown.toHTML(o.html);
	}

	checkDialogForm();

	$('#dialog-common .dialog-content').html(o.html);

	$('#dialog-common').dialog({
		modal: true,
		title: o.title,
		width: o.width,
		height: o.height,
		buttons: [{
			text:o.button_name,
			click: function(){
				$(this).dialog('close');
			}
		}],
		close: function(){}
	});
}

function setVideoMode(val)
{
	$("input[type=radio][name=videoMode][value="+ val +"]").attr("checked",'checked');
}

function successTipBox(html){
	var Dialog_ID_Prefix = "successTipBox_";
	var time = new Date();
	var Dialog_ID = Dialog_ID_Prefix+time.getTime();
	var ClassName = 'SuccessTipBox';

	$('<div id="'+Dialog_ID+'" class="'+ClassName+'"><span class="title">'+html+'</span><span class="tip_btn"><img src="/static/images/tip_icon_success2.gif" onClick="javascript:$(this).parent().parent().remove();" style="cursor:pointer;" /></span></div>').appendTo($('body'));

	var box = $('#'+Dialog_ID);

	var x = ($(window).width())-(box.outerWidth()+150);

	box.animate({top: '+84px',left: x+'px',opacity: 'hide'}, 0);
	box.animate({opacity: 'show'}, 300,function(){
		setTimeout(function(){
			box.animate({opacity: 'hide'}, 300,function(){
				box.remove();
			});
		},2000);
	});
}

function unloadPageVals(){}

//获取src中最后一个/和?号之间的值,真实的不带参数的
function getRealSrc(src)
{
	var realSrc = src;
	if(realSrc.indexOf("?") != -1)
	{
		realSrc = realSrc.split("?")[0];
	}
	var strs = realSrc.split("/");
	var realSrc = strs[strs.length-1];
	//statistics要特殊处理,追加sType参数
	if(realSrc == 'statistics')
	{
		var sType = getParaBySrc(src,'sType');
		realSrc = realSrc+'?sType='+sType;
	}
	return realSrc;
}

//获取src中传递参数的某个值
function getParaBySrc(src,para)
{
	var paraValue;
	if(src.indexOf("?") != -1)
	{
		src = src.split("?")[1];
	}
	var strs = src.split("&");
	for(var i = 0; i < strs.length; i ++)
	{
		if(strs[i].split("=")[0] == para)
		{
			paraValue = unescape(strs[i].split("=")[1]);
		}
	}
	return paraValue;
}

//设置地址栏
function setAddressBarSrc(src)
{
	var putSrc = true;
	//检查该src中toUrl否已存在
	if(getParaBySrc(location.href,'toUrl') == src)
	{
		putSrc = false;
	}
	if(src != 'xHome' && putSrc)
	{
		var strs = src.split("/");
		var currentSrc = strs[strs.length-1];
		location.hash = currentSrc;
	}
}

//设置左边高亮显示条
function setLeft(src)
{
	$("a[class='left_a6_s']").each(function(i){
		$(this).attr('class','left_a6');
	});
	$("a[class='left_a6']").each(function(i){
		var href = getRealSrc($(this).attr('realhref'));
		if(src.indexOf(href+'_body')==-1)
		{

		}
		else
		{
			$(this).attr('class','left_a6_s');
			return false;
		}
	});
}

function resizeright()
{
	var width = $('.spmc_box').width();

	var right = width- 230;
	if(right>0)
	{
		$('.right').width(right);
	}
}

function setLeftHeight()
{
	var left_bg_height = $('.right').outerHeight(true)+$('.help_navigation').outerHeight(true);
	if(left_bg_height>$('.base_left').outerHeight(true))
	{
		$('#left_bg').css('height',left_bg_height);
	}
	else
	{
		$('#left_bg').css('height',$('.base_left').outerHeight(true));
	}
}

function loadAjaxDiv(src,callbackfun){

	setLeft(src);
	$.ajax({
		url: src,
		type: 'GET',
		error: function(){alert('读取数据失败,请刷新后重试.url='+src);},
		success: function(data){
			//调用释放全局对象的函数和初始化
			beWaitingResult = false;
			ORGarr = [];
			selectOrgTreeItem = null;
			unloadPageVals();

			//载入html
			document.getElementById("xAjaxRight").innerHTML = '';
			$('#xAjaxRight').append(data);
			$(document).scrollTop(0);

			setLeftHeight();
			if(typeof(callbackfun) == 'function')
			{
				callbackfun(src);
			}
		}
	});

}

function myloadAjaxDiv(src,callbackfun)
{
	$('body a:not([target]),a[target=_self],a[target=""]').each(function(i){
		if($(this).attr('realhref')==src){
			$(this).parent().parent().siblings().find('.left_b_c').slideUp(600);
			$(this).parent().parent().siblings().attr("slide","slideUp");
		}
	});
	loadAjaxDiv(src,callbackfun);
}


function toAjaxLink(){
	$('body a:not([target]),a[target=_self],a[target=""]').each(function(i){
		if($(this).attr('href')){
			var link = $(this).attr('href');
			if ($(this).attr('href').toLowerCase().indexOf('jumppage')!=-1)
			{
			}
			else if ($(this).attr('href').toLowerCase().indexOf('#') == (link.length)-1){//#在最后面
				$(this).attr('href','javascript:');
			}else if($(this).attr('href').toLowerCase().indexOf('#') == 0){//#在最前面
			}else if($(this).attr('href').toLowerCase().indexOf('javascript:') == 0){
			}else{
				var timestamp=Math.round(new Date().getTime()/1000);
				$(this).attr('href','javascript:myloadAjaxDiv("'+encodeURI(link+'_body/?timestamp='+timestamp)+'");');
			}
		}
	});
}

function reJump(){
	var tourl = '';
	if(tourl == '')
	{//如果是刷新
		var hash = location.hash.replace('#','');
		if(hash)
		{
			tourl = hash;
			//如果是IE location.hash 可能会取不到?后面的值,故人工增加
			var src = location.search;
			if(Sys.ie && src.indexOf("?") != -1 && tourl.indexOf("?") == -1){
				tourl = tourl+src;
			}
		}
	}
	if(tourl == null || tourl == "" || tourl == undefined)
	{//如果toUrl参数
		var src = location.href;
		tourl = getParaBySrc(src,'toUrl');
	}
	if(tourl == null || tourl == "" || tourl == undefined)
	{//如果是home页面
		var src = location.href;
		var realSrc = getRealSrc(src);
		if(realSrc == 'home')
		{
			tourl = 'xHome';
		}
	}
	if(tourl)
	{
		loadAjaxDiv(tourl);
	}
	setTimeout("setTitle()",500);
}

//设置被改写的Title
function setTitle()
{
	if(document.readyState == "complete" && document.title != pageTitle)
	{
		document.title = pageTitle;
	}
	setTimeout("setTitle()",500);
}

function checkDialogForm()
{
	if($('#dialog-confirm').attr('id') == undefined)
	{
		var div = '<div id="dialog-confirm" class="ajaxForm"><div class="dialog-icon"></div><div class="dialog-msg"></div><form method="post" id="dialog-confirm-form" name="dialog-confirm-form"><input type="hidden" name="id" /><input type="hidden" name="extdata" /></form></div>';
		$('body').append(div);
	}
	if($('#dialog-common').attr('id') == undefined)
	{
		var div = '<div id="dialog-common" class="ajaxForm"><div class="dialog-content"></div></div>';
		$('body').append(div);
	}
}

function FromatTime(now)
{
	var year = now.getFullYear();       //年
	var month = now.getMonth() + 1;     //月
	var day = now.getDate();            //日
	var hh = now.getHours();            //时
	var mm = now.getMinutes();          //分
	var clock = year + "-";
	if(month < 10)
		clock += "0";
	clock += month + "-";
	if(day < 10)
		clock += "0";
	clock += day;
	return(clock);
}

function FromatTime2(now)
{
	var year = now.getFullYear();       //年
	var month = now.getMonth() + 1;     //月
	var day = now.getDate();            //日
	var hh = now.getHours();            //时
	var mm = now.getMinutes();          //分
	var ss = now.getSeconds();          //秒
	var clock = year + "-";
	if(month < 10)
		clock += "0";
	clock += month + "-";
	if(day < 10)
		clock += "0";
	clock += day;

	clock += " ";
	if(hh < 10)
		clock += "0";
	clock += hh;
	clock += ":";
	if(mm < 10)
		clock += "0";
	clock += mm;
	clock += ":";
	if(ss < 10)
		clock += "0";
	clock += ss;
	return(clock);
}

function CurentTime()
{
	return FromatTime(new Date());
}

function DateSubDay(date,subday)
{
	var d = new Date(date.replace(/-/g, '/'));
	d.setDate(d.getDate() - subday); // 前7天
	return FromatTime(d);
}

function DateAddSeconds(date,Seconds)
{
	var d = new Date(date.replace(/-/g, '/'));
	d.setSeconds(d.getSeconds() + Seconds);
	return FromatTime2(d);
}

function myAjaxGet(serverurl,params,successcallback)
{
	var p=arguments[3]?arguments[3]:null;
	$.ajax({
		url:serverurl, //后台处理程序
		type:'get',         //数据发送方式
		dataType:'json',     //接受数据格式
		data:params,         //要传递的数据
		success:function(result) {
			successcallback(result,p);
        }, //回传函数(这里是函数名)
		error:function(x,e)
		{
			if( x.status == 200 )
			{
				return;
			}
			else if( x.status == 504 )
			{
				debugger;
			}
			else if( x.status != 0 )
			{
				show_error(x.status, x.responseText);
			}
		}

	});
}

function _show_dialog_error(obj) {
    $(obj).siblings('pre').show();
}

function show_error(code, error) {
    var c = $('<div></div>'),
        h = $('<h4></h4>').text('内部错误，错误码：'+ code),
        sp = $('<p style="color: blue;cursor: pointer" onclick="_show_dialog_error(this)">获取更多信息</p>')
        body = $('<pre style="display: none"></pre>').text(error);
    c.append(h).append(sp).append(body);
    openErrorDialog({title:'错误',html:c, width:500, height:'auto'});
}

function myAjaxPost(serverurl,params,successcallback)
{
	var p=arguments[3]?arguments[3]:null;
	$.ajax({
		url:serverurl, //后台处理程序
		type:'post',         //数据发送方式
		dataType:'json',     //接受数据格式
		data:params,         //要传递的数据
		beforeSend: function(xhr, settings){
			var csrftoken = $.cookie('csrftoken');
			xhr.setRequestHeader("X-CSRFToken", csrftoken);
		},
		success:function(result) {
			successcallback(result,p);
        }, //回传函数(这里是函数名)
		error:function(x,e)
		{
			if( x.status == 200 )
			{
				return;
			}
			else if( x.status == 504 )
			{
				debugger;
			}
			else if( x.status != 0 )
			{
				show_error(x.status, x.responseText);
			}
		}

	});
}

function myJsonp(url,errdiv,successcallback,p)
{
	var timeout=arguments[4]?arguments[4]:6000;
	$.ajax({
        url:url,
        dataType:'jsonp',
        data:'',
        jsonp:'callback',
        success:function(result) {
			successcallback(result,p);
        },
		error:function(x,e){
			var html= '';
			if(window.location.protocol == 'https:')
			{
				var html='当前启用了https，请设置浏览器不要拦截'+url;
				html+="<br /><br />状态："+x.status+",错误描述："+e;
			}
			else
			{
				html='无法解析服务器数据：'+url+"<br /><br />";
				html+="状态："+x.status+",错误描述："+e;
			}
			$('#'+errdiv).html(html);
			$('#'+errdiv).css('word-break', 'break-all');
			$('#'+errdiv).show();
			if (typeof(change_to_checked) == 'function'){
				if (!has_set_interval){
        			change_to_checked(4,'查询成功');
    			}
			}

		},
        timeout:timeout
    });
}

function myPostBinary(serverurl,binary,successcallback)
{
	$.ajax({
		url:serverurl, //后台处理程序
		type:'post',         //数据发送方式
		dataType:'json',     //接受数据格式
		data:binary,         //要传递的数据
		processData: false,
		beforeSend: function(xhr, settings){
			var csrftoken = $.cookie('csrftoken');
			xhr.setRequestHeader("X-CSRFToken", csrftoken);
			xhr.setRequestHeader("Content-Type",'application/octet-stream');
		},
		success:successcallback, //回传函数(这里是函数名)
		error:function(x,e)
		{
			if( x.status == 200 )
			{
				return;
			}
			if( x.status != 0 )
			{
				alert("status="+x.status+",e="+e);
			}
		}

	});
}

function SilenceCallback(jsonstr)
{
	if(jsonstr.r!=0)
	{
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
}

function TipCallback(jsonstr)
{
	if(jsonstr.r!=0)
	{
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	successTipBox("操作成功");
}

function CheckBrowserVer()
{
	if (window.FileReader)
	{
		//html5
		return true;
	}
	var browser=navigator.appName;
	var b_version=navigator.appVersion;
	var version=b_version.split(";");
	var trim_Version=version[1].replace(/[ ]/g,"");
	if(browser=="Microsoft Internet Explorer" && trim_Version=="MSIE6.0")
	{
		return false;
	}
	else if(browser=="Microsoft Internet Explorer" && trim_Version=="MSIE7.0")
	{
		return false;
	}
	else if(browser=="Microsoft Internet Explorer")
	{
		return true;
	}

	return false;
}

// startPage，endPage，pageNums：字符串参数
function CheckerInputRange(startPage, endPage, pageNums) {
    startPage = parseInt(startPage);
    endPage = parseInt(endPage);
    pageNums = parseInt(pageNums);

	if(pageNums == 0){
		openErrorDialog({title:'错误',html:'暂无记录可导出'});
		return false;
	}

	if(isNaN(startPage) || isNaN(endPage) || startPage < 1 || endPage > pageNums || startPage > endPage){
        openErrorDialog({title:'错误',html:'页数范围输入无效，页数范围：1--' + pageNums});
        return false;
    }

    return true;
}

function MorrisArea(div_id, data, xkey, ykeys, labels, areaColors) {
	Morris.Area({
	  element: div_id,
	  data: data,
	  xkey: xkey,
	  ykeys: ykeys,
	  labels: labels,
	  pointSize: 0,
	  lineWidth: 0,
	  hideHover: 'auto',
	  behaveLikeLine: true,
	  lineColors: areaColors,
	  smooth: false
	});
}

function IsURL(str_url) {
	var strRegex = "^https?://"
			+ "?(([0-9a-z_!~*'().&=+$%-]+: )?[0-9a-z_!~*'().&=+$%-]+@)?" //ftp的user@
			+ "(([0-9]{1,3}\.){3}[0-9]{1,3}" // IP形式的URL- 199.194.52.184
			+ "|" // 允许IP和DOMAIN（域名）
			+ "([0-9a-z_!~*'()-]+\.)*" // 域名- www.
			+ "([0-9a-z][0-9a-z-]{0,61})?[0-9a-z]\." // 二级域名
			+ "[a-z]{2,6})" // first level domain- .com or .museum
			+ "(:[0-9]{1,5})?" // 端口- :80
			+ "((/?)|" // a slash isn't required if there is no file name
			+ "(/[0-9a-z_!~*'().;?:@&=+$,%#-]+)+/?)$";
	var re = new RegExp(strRegex, 'i');
	return re.test(str_url);
}

function IsDomain(domain) {
	if(String(domain).indexOf('http') == 0){
		return false;
	}
	if(String(domain).indexOf(':') != -1){
		return false;
	}

	var re = new RegExp('[a-zA-Z0-9][-a-zA-Z0-9]{0,62}(\.[a-zA-Z0-9][-a-zA-Z0-9]{0,62})+\.?');
	return re.test(domain);
}

// 展示loading,div container must have set : position: relative!
jQuery.fn.waite_s = function() {
    var e = $(this[0]); // It's your element
	if (e.find('.auto_waite').length){
	    return this;
    }
    var my_waite_css = {
        "display": "none",
        "z-index": "9999",
        "background": "#fff",
        "text-align": "center",
        "top": "0",
        "left": "0",
        "position": "absolute",
        "height": "100%",
        "width": "100%"
    }
    var _div = $('<div class="auto_waite"></div>').css(my_waite_css);
    var _div1 = $('<div class="my_waite_content"></div>');
    var _img = $('<img src="/static/images/loading.gif" alt="加载中" style="width: 30px;position: relative;top:10px" />');
    var span = $('<span style="font-size: 1.5em">加载数据中...</span>');
    _div1.append(_img).append(span);
    var obj = _div.append(_div1)
    e.append(obj);
    obj.find('.my_waite_content').css('margin-top',  e.height()/2-15);
    obj.show();
    return this; // This is needed so others can keep chaining off of this
};

// 隐藏loading
jQuery.fn.waite_h = function() {
    var e = $(this[0]) // It's your element
    e.find('.auto_waite').remove();
    return this; // This is needed so others can keep chaining off of this
};

// 上传文件到后台, 返回文件路径(临时的), 完成后执行回调函数
function uplodePreBackupShell(form$, postUrl, uplodeCallbk) {
	$.ajax({
		url: postUrl,
		type: 'POST',
		cache: false,
		data: new FormData(form$[0]),
		dataType: 'json',
		processData: false,
		contentType: false,
		beforeSend: function (xhr, settings) {
			var csrftoken = $.cookie('csrftoken');
			xhr.setRequestHeader("X-CSRFToken", csrftoken);
		}

	}).done(function (res) {
		if (res.r === 0) {
			uplodeCallbk(true, res.filepath);
		}
		else {
			uplodeCallbk(false, res.e);
		}

	}).fail(function (res) {
		uplodeCallbk(false, '上传文件时出现未知错误, 可能网络异常');
	});

	return null;
}

function get_shell_infos_from_ui(tabsShell$) {
	return {
		'exe_name': tabsShell$.find('input[name=exe_name]').val(),
		'params':  tabsShell$.find('input[name=exe_params]').val(),
		'work_path': tabsShell$.find('input[name=work_path]').val(),
		'unzip_path': tabsShell$.find('input[name=unzip_path]').val(),
		'zip_file': tabsShell$.find('input[name=script_zip]').val(),
		'ignore_shell_error': tabsShell$.find('#ignore_shell_error').prop('checked')
	}
}

function init_shell_infos_from_ui(tabsShell$) {
 	tabsShell$.find('input[name=exe_name]').val('');
 	tabsShell$.find('input[name=exe_params]').val('');
	tabsShell$.find('input[name=work_path]').val('');
	tabsShell$.find('input[name=unzip_path]').val('');
	tabsShell$.find('input[name=script_zip]').val('');
	tabsShell$.find('input[name=script_zip]').prop('last_zip', '');
	tabsShell$.find('#ignore_shell_error').prop('checked', true);
}

function set_shell_infos_from_ui(tabsShell$, shellInfos) {
 	tabsShell$.find('input[name=exe_name]').val(shellInfos.exe_name);
 	tabsShell$.find('input[name=exe_params]').val(shellInfos.params);
	tabsShell$.find('input[name=work_path]').val(shellInfos.work_path);
	tabsShell$.find('input[name=unzip_path]').val(shellInfos.unzip_path);
	tabsShell$.find('input[name=script_zip]').prop('last_zip', shellInfos.zip_path);		// Zip文件在AIO的路径
	tabsShell$.find('#ignore_shell_error').prop('checked', shellInfos.ignore_shell_error);	// 是否忽略执行异常
}

function is_enable_shell(tabsShell$) {
	var shell_infos = get_shell_infos_from_ui(tabsShell$);
	return shell_infos.zip_file !== '' || shell_infos.exe_name !== '' || shell_infos.work_path !== '' || shell_infos.unzip_path !== '';
}

function is_enable_shell_and_new_zip(tabsShell$) {
	var shell_infos = get_shell_infos_from_ui(tabsShell$);
	return is_enable_shell(tabsShell$) && shell_infos.zip_file !== ''
}

function is_enable_shell_and_not_zip(tabsShell$) {
	var shell_infos = get_shell_infos_from_ui(tabsShell$);
	return is_enable_shell(tabsShell$) && shell_infos.zip_file === ''
}

function is_exist_last_zip_in_aio(tabsShell$) {
	return tabsShell$.find('input[name=script_zip]').prop('last_zip') !== '';
}

// 判断网关是否可达
function gateway_reachable(ip, mask, gateway) {
	if (isIPV4(ip) && isMask(mask) && isIPV4(gateway)){
        var to_int = function (x) {
	            return parseInt(x);
            },
            ip_items = ip.split('.').map(to_int),
	        mask_items = mask.split('.').map(to_int),
	        gateway_items = gateway.split('.').map(to_int);
		for(var i=0; i < 4; i ++ ){
		    var l_v = ip_items[i] & mask_items[i],
                r_v = gateway_items[i] & mask_items[i];
		    if (l_v != r_v){
		        return false;
            }
        }
        return true;
	}
	return false;
}

function getDebugLogZip(ident) {
    myAjaxGet('/apiv1/logs/', 'action=get_zip_url&ident='+ident, function (jsonObj) {
        var url = jsonObj.url, msg = jsonObj.msg, ctime =jsonObj.ctime, show_html = '';
        if(url){
            show_html = '<a href="{0}" target="blank">{1}</a><br><br>生成时间：{2}'.replace('{0}', url).replace('{1}', url).replace('{2}', ctime);
        }
        else {
            show_html = msg;
        }

        openConfirmDialog({
            title: '调试日志',
            html: show_html,
            confirm_text: '重新生成',
            onBeforeOK: function(){
                $(this).dialog('close');
                myAjaxGet('/apiv1/logs/', 'action=regenerate_log_zip&ident='+ident, function (jsdata) {
                	if (!jsdata.result){
                		openCommonDialog({title: '信息', html: jsdata.msg});
					}
                });
            }
        });
    });
}

function showCheckingPeMsg(show) {
	var msgDiv = $('#pe-status-checking');
	return (show) ? (msgDiv.show()) : (msgDiv.hide());
}

function isShowedCheckingPeMsg() {
	return $('#pe-status-checking').is(":visible");
}

function isTargetPe(treeID) {
	var destserverid = getaciTreeChecked(treeID);
	var tmpid = GetAciTreeParent(treeID, destserverid);
	return tmpid === 'ui_2';
}

function checkingPeStatus(tabs$, next, pe_ident , callBk) {
	showCheckingPeMsg(true);
	myAjaxGet('../restore_handle/?a=check_pe_status', 'pe_ident=' + pe_ident, function (jsonData) {
		showCheckingPeMsg(false);
		if(jsonData.is_linked){
			tabs$.tabs( "option", "active", next);
			callBk();
		}
		else {
			openCommonDialog({title:'信息', html:"目标客户端当前处于[离线状态]或[初始化中], 请稍后再试!"});
		}
	});
}

function DiskCheck() {
    this.disk = [];
    this.msg_tmp = '<p>硬盘[{src_name}]:</p>' +
		'<p style="text-indent: 2em">将恢复到容量较小的硬盘[{dst_name}]上，此操作会丢失硬盘尾部[{value}]数据。</p>';
    this.check = function (disk) {
        if (this.disk.indexOf(disk.ident) == -1){
            this.disk.push(disk.ident);
            return this._format(disk);
        }else{
            return ''
        }
    };
    this._format = function (disk) {
        return this.msg_tmp.replace('{src_name}', disk.src_name).replace('{dst_name}', disk.dst_name).replace('{value}', disk.value);
    };
    this.init = function () {
		this.disk = [];
    }
}

function set_html_elem_title_prop(str_elem, sn_val) {
	if(sn_val){
		return $(str_elem).prop('title', '磁盘序列号: '+sn_val);
	}

	return str_elem
}

// 正整数字符串
function positive_integer(input) {
	var patrn=/^[0-9]*[1-9][0-9]*$/;
	return !!patrn.exec(input);
}

// 正整数字符串, 且在指定范围内 （包含边界）
function is_positive_num_and_in_range(num, start, end) {
	if(!positive_integer(num)){
		return false;
	}

	return !(parseInt(num) < parseInt(start) || parseInt(num) > parseInt(end));
}

// 正整数字符串, 且大于等于NUM
function is_positive_num_and_gte(num, NUM) {
	if(!positive_integer(num)){
		return false;
	}

	return parseInt(num) >= parseInt(NUM);
}

// 获取表格一行数据
function get_select_row(grid_id) {
    var ids = $('#' + grid_id).jqGrid('getGridParam', 'selarrrow');
    if (ids.length == 1){
        return $('#' + grid_id).jqGrid('getRowData', ids[0]);
    }else{
        return ''
    }
}

Date.prototype.Format = function (fmt) {
	/*
	var o = {
		"M+": this.getMonth() + 1,
		"d+": this.getDate(),
		"h+": this.getHours(),
		"m+": this.getMinutes(),
		"s+": this.getSeconds(),
		"q+": Math.floor((this.getMonth() + 3) / 3),
		"S": this.getMilliseconds()
    };
    if (/(y+)/.test(fmt)) fmt = fmt.replace(RegExp.$1, (this.getFullYear() + "").substr(4 - RegExp.$1.length));
    for (var k in o)
    if (new RegExp("(" + k + ")").test(fmt)) fmt = fmt.replace(RegExp.$1, (RegExp.$1.length == 1) ? (o[k]) : (("00" + o[k]).substr(("" + o[k]).length)));
    return fmt;
	*/
    // 以上方法会将 2019-01-23 17:22:00.001 转化成 2019-01-23 17:22:00.1  毫秒会扩大 造成数据不正确
    // 调用此接口，都统一返回 YYYY-MM-DD HH:mm:ss.SSS 格式
    return moment(this).format("YYYY-MM-DD HH:mm:ss.SSS");
};

function nameCheck(name){
	if (name == '') {
		return '名称不能为空';
	}
	var pattern = new RegExp("[`~!@#$^&*=+{}\|:;\"',<>{}?/\\\\ ]");
	if(pattern.test(name)){
		var msg = '名称' + '"' + name +'"' + '包括不允许的字符。不允许的字符包括' +
					'`~!@#$^&*=+{}|:;"\',<>{}?/\\\\和空格';
		return msg;
	}
	return '';
}
