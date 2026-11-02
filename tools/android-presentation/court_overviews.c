/* SPDX-License-Identifier: GPL-2.0-or-later
 * Deterministic Cairo 1.18 overview generation; original artwork is read-only.
 * Build utility only. Run from tools/native-account-game/ui-assets/court.
 */
#include <cairo.h>
#include <stdio.h>
int main(void) {
    const char *names[] = {"world", "area"};
    for (unsigned i=0;i<2;++i) {
        char source[64], target[64];
        snprintf(source,sizeof source,"%s.png",names[i]);
        snprintf(target,sizeof target,"%s-overview.png",names[i]);
        cairo_surface_t *in=cairo_image_surface_create_from_png(source);
        if(cairo_surface_status(in)!=CAIRO_STATUS_SUCCESS)return 1;
        int width=cairo_image_surface_get_width(in),height=cairo_image_surface_get_height(in);
        int new_width=1024,new_height=(height*1024+width/2)/width;
        cairo_surface_t *out=cairo_image_surface_create(CAIRO_FORMAT_RGB24,new_width,new_height);
        cairo_t *c=cairo_create(out);cairo_scale(c,(double)new_width/width,(double)new_height/height);
        cairo_set_source_surface(c,in,0,0);cairo_pattern_set_filter(cairo_get_source(c),CAIRO_FILTER_BEST);cairo_paint(c);
        if(cairo_status(c)!=CAIRO_STATUS_SUCCESS||cairo_surface_write_to_png(out,target)!=CAIRO_STATUS_SUCCESS)return 2;
        printf("%s %dx%d\n",target,new_width,new_height);
        cairo_destroy(c);cairo_surface_destroy(out);cairo_surface_destroy(in);
    }
    return 0;
}
