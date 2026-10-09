const MAX_BYTES=12*1024*1024;
export async function readArtwork({art_url,art_file}) {
  let blob,source=null;
  if(art_file instanceof File&&art_file.size){if(art_file.size>MAX_BYTES)throw Error('Choose an image smaller than 12 MB.');blob=art_file;}
  else {
    let url;try{url=new URL(art_url);}catch{throw Error('Enter a complete HTTPS image URL, or choose an image file.');}
    if(url.protocol!=='https:'||url.username||url.password)throw Error('Use a direct HTTPS image URL without login details.');
    source=url.href;
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
    try {
      const response=await fetch(source,{mode:'cors',credentials:'omit',referrerPolicy:'no-referrer',signal:controller.signal});
      if(!response.ok)throw Error('The image server refused this URL.');
      if(Number(response.headers.get('content-length'))>MAX_BYTES)throw Error('Choose an image smaller than 12 MB.');
      const reader=response.body.getReader(),parts=[];let total=0;
      for(;;){const {done,value}=await reader.read();if(done)break;total+=value.length;if(total>MAX_BYTES){await reader.cancel();throw Error('Choose an image smaller than 12 MB.');}parts.push(value);}
      blob=new Blob(parts,{type:response.headers.get('content-type')||'application/octet-stream'});
    }catch(error){if(error.name==='TypeError')throw Error('This image host does not allow browser access. Download the image and choose the file instead.');if(error.name==='AbortError')throw Error('The image took too long to download. Try a file instead.');throw error;}
    finally{clearTimeout(timer);}
  }
  let image;try{image=await createImageBitmap(blob);}catch{throw Error('This is not a supported image. Try a PNG, JPEG, or WebP file.');}
  const pixels=image.width*image.height;image.close();if(pixels>16000000)throw Error('Choose an image under 16 megapixels.');
  return {blob,source};
}
